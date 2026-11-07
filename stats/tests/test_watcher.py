import asyncio, os, sys, time
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cs2stats import ingest
from cs2stats.db import DB
from cs2stats.model import MatchData, Round, Kill, T, CT
from cs2stats.stats import compute


def fake_analyze(path):
    ro = {str(i): (T if i < 5 else CT) for i in range(10)}
    rounds = [Round(n, winner=T, start_tick=n * 1000, roster=dict(ro),
                    kills=[Kill(n * 1000 + j, str(j), str(j + 5), weapon="ak47") for j in range(5)])
              for n in range(1, 14)]
    md = MatchData("de_mirage", os.path.basename(path), rounds, {str(i): f"p{i}" for i in range(10)})
    P, s = compute(md)
    return md.source_name, "7", "2026-11-05T20:00:00", s, P


def test_watcher_imports_finished_demo_and_skips_growing(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "analyze_demo", fake_analyze)
    old = tmp_path / "old.dem"; old.write_bytes(b"x")
    os.utime(old, (time.time() - 600, time.time() - 600))
    fresh = tmp_path / "fresh.dem"; fresh.write_bytes(b"x")
    announced = []

    async def on_new(mid): announced.append(mid)

    db = DB(str(tmp_path / "s.db"))
    w = ingest.DemoWatcher(db, str(tmp_path), on_new, poll=0.05, stable_seconds=0.3)
    w.pool = ThreadPoolExecutor(1)
    w.pause = 0

    async def go():
        task = asyncio.create_task(w.run())
        await asyncio.sleep(0.15)
        assert db.has("old.dem") and not db.has("fresh.dem")   # backfilled, still-fresh skipped
        assert announced == []                                  # backfill isn't announced
        await asyncio.sleep(0.6)
        task.cancel()
    asyncio.run(go())
    assert db.has("fresh.dem") and len(announced) == 1
    m, ps = db.match(announced[0])
    assert m["score_a"] == 13 and len(ps) == 10


def _summary(**kw):
    s = {"rounds": 24, "score_a": 13, "score_b": 11, "map": "de_x", "game_mode": "competitive", "purchases": 900}
    s.update(kw)
    return s


def test_filters_modes_retakes_unfinished():
    from cs2stats.stats import PlayerMatch
    P = {str(i): PlayerMatch(str(i), team="A" if i < 5 else "B", rounds=24) for i in range(10)}
    q = lambda **kw: ingest.qualifies(_summary(**kw), P, 10, 4)
    assert q() is None
    assert "game mode" in q(game_mode="scrimcomp2v2")
    assert "game mode" in q(game_mode="casual")
    assert "retakes" in q(purchases=0, rounds=21, score_a=11, score_b=10)
    assert q(purchases=None) is None                       # unknown -> don't block
    assert "unfinished" in q(score_a=12, score_b=13, rounds=25)
    assert q(score_a=16, score_b=14, rounds=30) is None     # overtime win
    assert q(score_a=5, score_b=13, rounds=18) is None


def test_demo_replaces_log_backfill(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "analyze_demo", fake_analyze)
    db = DB(str(tmp_path / "s.db"))
    _, _, _, s, P = fake_analyze("m.dem")
    s.update(game_mode="competitive", purchases=500)
    db.save_match("m.dem", s, P, source="log")
    announced = []

    async def on_new(mid): announced.append(mid)
    w = ingest.DemoWatcher(db, str(tmp_path), on_new)
    w.pool = ThreadPoolExecutor(1)
    w.pause = 0
    (tmp_path / "m.dem").write_bytes(b"x")
    mid, why = asyncio.run(w.process(str(tmp_path / "m.dem")))
    assert mid and why is None and db.has_demo("m.dem") and announced == []
    assert len(db.recent_matches()) == 1


def test_skipped_demo_not_reread_after_restart(tmp_path, monkeypatch):
    calls = []

    def short_demo(path):
        calls.append(path)
        src, mz, played, s, P = fake_analyze(path)
        s["rounds"] = 5                                  # too short -> skipped
        return src, mz, played, s, P
    monkeypatch.setattr(ingest, "analyze_demo", short_demo)
    d = tmp_path / "short.dem"; d.write_bytes(b"x")
    os.utime(d, (time.time() - 600, time.time() - 600))
    db = DB(str(tmp_path / "s.db"))

    async def nothing(mid): pass

    async def run_once():
        w = ingest.DemoWatcher(db, str(tmp_path), nothing, poll=0.05, stable_seconds=0.3)
        w.pool = ThreadPoolExecutor(1)
        w.pause = 0
        task = asyncio.create_task(w.run())
        await asyncio.sleep(0.3)
        task.cancel()
    asyncio.run(run_once())
    asyncio.run(run_once())                               # "restart"
    assert len(calls) == 1 and "short.dem" in db.demo_keys()
