import json, os, sys, threading, urllib.request, urllib.error
from http.server import ThreadingHTTPServer
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cs2stats import server
from cs2stats.model import MatchData, Round, Kill, T, CT
from cs2stats.stats import compute

SIDS = [str(76561198000000000 + i) for i in range(10)]


def fake_match(db, announce):
    ro = {s: (T if i < 5 else CT) for i, s in enumerate(SIDS)}
    rounds = [Round(n, winner=T, start_tick=n * 1000, roster=dict(ro),
                    kills=[Kill(n * 1000 + j, SIDS[j], SIDS[j + 5], weapon="ak47") for j in range(5)])
              for n in range(1, 14)]
    P, s = compute(MatchData("de_mirage", "x.dem", rounds, {x: f"p{i}" for i, x in enumerate(SIDS)}))
    return db.save_match(f"m{announce}.dem", s, P, played_at="2026-11-05T20:00:00", announce=announce)


def start(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "s.db"))
    monkeypatch.setenv("BACKFILL_DB", str(tmp_path / "none.db"))
    monkeypatch.setenv("STATS_API_KEY", "secret")
    svc = server.StatsService()
    server.Handler.svc = svc
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return svc, f"http://127.0.0.1:{httpd.server_port}"


def call(url, method="GET", body=None, key=None):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body else None)
    if key:
        req.add_header("Authorization", "Bearer " + key)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_api_flow(tmp_path, monkeypatch):
    svc, base = start(tmp_path, monkeypatch)
    fake_match(svc.db, announce=False)
    mid = fake_match(svc.db, announce=True)

    code, s = call(base + "/api/summary")
    assert code == 200 and s["matches"] == 2 and s["players"] == 10
    code, m = call(base + f"/api/matches/{mid}")
    assert m["match"]["round_log"] == "a" * 13 and m["highlights"][0]["key"] == "mvp"
    code, p = call(base + f"/api/players/{SIDS[0]}")
    assert p["player"]["matches"] == 2 and len(p["history"]) == 2

    # bot-only endpoints need the key
    assert call(base + "/api/announcements")[0] == 401
    code, a = call(base + "/api/announcements", key="secret")
    assert [x["match"]["id"] for x in a["matches"]] == [mid]
    assert call(base + "/api/link", "POST", {"discord_id": "42", "steamid": SIDS[0]}, key="wrong")[0] == 401
    code, r = call(base + "/api/link", "POST", {"discord_id": "42", "steamid": SIDS[0]}, key="secret")
    assert code == 200 and r["name"] == "p0"
    assert call(base + "/api/discord/42", key="secret")[1]["steamid"] == SIDS[0]
    code, a = call(base + "/api/announcements", key="secret")
    assert a["matches"][0]["discord"] == {SIDS[0]: "42"}
    call(base + f"/api/announcements/{mid}/done", "POST", key="secret")
    assert call(base + "/api/announcements", key="secret")[1]["matches"] == []

    # website: any non-API path serves the page
    with urllib.request.urlopen(base + "/player/" + SIDS[0]) as r:
        assert b"CS2 5v5 Leaderboard" in r.read()
    assert call(base + "/api/players/notanid")[0] == 400
