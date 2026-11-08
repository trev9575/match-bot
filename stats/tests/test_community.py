import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cs2stats import community, elo, icons
from cs2stats.db import DB
from cs2stats.model import MatchData, Round, Kill, T, CT
from cs2stats.stats import compute

SIDS = [str(76561198000000000 + i) for i in range(10)]


def match(winner_by_round, first_killer=0):
    """5v5; team A (SIDS 0-4) starts T. Each round the winners' player kills one enemy."""
    ro = {s: (T if i < 5 else CT) for i, s in enumerate(SIDS)}
    rounds = []
    for n, w in enumerate(winner_by_round, 1):
        side_ro = ro if n <= 12 else {s: (CT if sd == T else T) for s, sd in ro.items()}
        a_side = side_ro[SIDS[0]]
        win_side = a_side if w == "A" else (CT if a_side == T else T)
        if w == "A":
            k = [Kill(n * 1000 + j, SIDS[(first_killer + j) % 5], SIDS[5 + j], headshot=j == 0,
                      weapon="knife" if (n, j) == (1, 4) else "awp") for j in range(5)]
        else:
            k = [Kill(n * 1000 + j, SIDS[5 + j], SIDS[j], weapon="ak47") for j in range(5)]
        rounds.append(Round(n, winner=win_side, start_tick=n * 1000, roster=dict(side_ro), kills=k))
    return compute(MatchData("de_mirage", "x.dem", rounds, {x: f"p{i}" for i, x in enumerate(SIDS)}))


def test_kill_pairs_and_side_openings():
    P, s = match(["A"] * 13)
    pairs = {(a, v): (n, hs, op) for a, v, n, hs, op in s["pairs"]}
    assert pairs[(SIDS[0], SIDS[5])] == (13, 13, 13)        # opened every round with a headshot
    assert P[SIDS[0]].entry_wins_t == 12 and P[SIDS[0]].entry_wins_ct == 1
    assert P[SIDS[5]].entry_attempts_ct == 12 and P[SIDS[5]].entry_attempts_t == 1
    assert P[SIDS[4]].knife_kills == 1 and P[SIDS[0]].awp_kills == 13


def test_comeback_deficit():
    assert community.max_deficit("BBBBBBaaaaaaaaaaaaa", "A") == 6
    assert community.max_deficit("ab", "B") == 1


def save(db, rounds_won, when, key):
    P, s = match(rounds_won)
    return db.save_match(key, s, P, played_at=when)


def test_achievements_duos_head_to_head(tmp_path):
    db = DB(str(tmp_path / "s.db"))
    # a 6-round deficit turned around: comeback for team A
    save(db, ["B"] * 6 + ["A"] * 13, "2026-11-01T20:00:00", "a.dem")
    for d in range(2, 7):
        save(db, ["A"] * 13, f"2026-11-0{d}T20:00:00", f"{d}.dem")
    rows = db.all_rows()
    full = elo.compute_full(db.elo_rows(), now="2026-11-07T00:00:00")
    res = community.achievements(rows, full)
    a0 = res["players"][SIDS[0]]
    assert "comeback" in a0 and "placed" in a0 and "streak5" not in res["players"][SIDS[5]]
    assert a0["streak5"]["count"] == 1 and "knife" in res["players"][SIDS[4]]
    assert "steamroll" in a0 and a0["steamroll"]["count"] == 5
    first = db.match()[0]["id"]
    assert (SIDS[0], "comeback") in res["match_unlocks"][1] and first == 6
    cat = community.achievement_catalog(res, 10)
    assert all(icons.svg(a["id"]).startswith("<svg") for a in cat)
    names = db.names()
    pairs = db.kill_pairs()
    h = community.head_to_head(SIDS[0], rows, pairs, names)
    v5 = next(o for o in h["opponents"] if o["steamid"] == SIDS[5])
    assert v5["games"] == 6 and v5["wins"] == 6 and v5["kills"] > 0 and v5["openings_won"] > 0
    assert h["teammates"][0]["games"] == 6 and h["teammates"][0]["win_pct"] == 100
    sup = community.superlatives(rows, pairs, names)
    assert sup["best_duo"]["win_pct"] == 100 and sup["worst_duo"]["win_pct"] == 0
    assert sup["rivalry"] is not None
    rc = community.recap(rows, pairs, full, res, names, "2026-11-03T00:00:00", "2026-11-07T00:00:00")
    assert rc["matches"] == 4 and rc["top_kills"]["steamid"] in SIDS[:5]


def test_stale_demo_refresh(tmp_path):
    db = DB(str(tmp_path / "s.db"))
    mid = save(db, ["A"] * 13, "2026-11-01T20:00:00", "a.dem")
    with db.conn() as c:
        c.execute("UPDATE matches SET detail_version=1")
        c.execute("DELETE FROM kill_pairs")
    assert db.stale_demos() == {"a.dem": mid}
    P, s = match(["A"] * 13)
    db.refresh_details(mid, s, P)
    assert db.stale_demos() == {} and len(db.kill_pairs()) == 5


def test_badges_are_valid_svg():
    import xml.dom.minidom
    for aid in community.ACH:
        for locked in (False, True):
            xml.dom.minidom.parseString(icons.svg(aid, 64, locked))   # duplicate attributes break <img>
