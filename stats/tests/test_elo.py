import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cs2stats import elo


def test_glicko2_matches_glickman_paper_example():
    # Glickman, "Example of the Glicko-2 system": 1500/200/0.06 vs three opponents
    r, rd, vol = elo.glicko2_update(1500, 200, 0.06, [(1400, 30, 1), (1550, 100, 0), (1700, 300, 0)], min_rd=0)
    assert abs(r - 1464.06) < 0.05 and abs(rd - 151.52) < 0.05 and abs(vol - 0.05999) < 0.00001


def rows_for(matches):
    out = []
    for mid, (date, a, b, winner) in enumerate(matches, 1):
        for t, team in (("A", a), ("B", b)):
            for sid in team:
                out.append({"match_id": mid, "played_at": date, "steamid": sid, "team": t,
                            "result": "T" if winner is None else ("W" if winner == t else "L")})
    return out


def test_team_results_move_ratings_and_placement():
    A, B = ["a1", "a2", "a3", "a4", "a5"], ["b1", "b2", "b3", "b4", "b5"]
    games = [(f"2026-11-0{d}T20:00:00", A, B, "A") for d in range(1, 6)]
    players, per = elo.compute(rows_for(games), now="2026-11-06T00:00:00")
    assert players["a1"]["elo"] > 1500 > players["b1"]["elo"]
    assert players["a1"]["placed"] and players["a1"]["rank"] > players["b1"]["rank"]
    assert per[(1, "a1")]["elo_before"] == 1500 and per[(1, "a1")]["elo_delta"] > 0
    # first-match swing is big (uncertain), later ones smaller
    assert per[(1, "a1")]["elo_delta"] > per[(5, "a1")]["elo_delta"] > 0
    few, _ = elo.compute(rows_for(games[:2]), now="2026-11-06T00:00:00")
    assert not few["a1"]["placed"] and few["a1"]["rank"] == 0 and "2/5" in few["a1"]["rank_name"]
    idle, _ = elo.compute(rows_for(games), now="2027-01-30T00:00:00")
    assert idle["a1"]["inactive"] and idle["a1"]["rank_name"] == "Rank expired"


def test_tie_keeps_equal_teams_level_and_rank_bands():
    players, per = elo.compute(rows_for([("2026-11-01T20:00:00", ["x"], ["y"], None)]))
    assert per[(1, "x")]["elo_delta"] == 0
    assert elo.rank_for(1500)["rank_short"] == "GN3" and elo.rank_for(800)["rank"] == 1
    assert elo.rank_for(2500)["rank_name"] == "The Global Elite"


def test_win_contribution_scales_elo_change():
    A, B = ["a1", "a2"], ["b1", "b2"]
    rows = rows_for([("2026-11-01T20:00:00", A, B, "A")])
    perf = {"a1": 1.6, "a2": 0.5, "b1": 1.5, "b2": 0.4}
    for r in rows:
        r["rating"] = perf[r["steamid"]]
    _, per = elo.compute(rows)
    d = {s: per[(1, s)]["elo_delta"] for s in perf}
    assert d["a1"] > d["a2"] > 0                 # both win; the carry gains more
    assert d["b2"] < d["b1"] < 0                 # both lose; the weak game costs more
    assert d["a1"] / d["a2"] < 1.3 / 0.7 + 0.01  # capped: at most +/-30%
    assert elo.contribution(None, 1.0) == 1.0 and elo.contribution(3.0, 1.0) == 1.3


def test_seasons_soft_reset_and_snapshots():
    assert elo.season_of("2026-11-07T10:00:00") == 1 and elo.season_of("2027-01-02T10:00:00") == 2
    assert elo.season_bounds(2) == ("2027-01-01T00:00:00", "2027-04-01T00:00:00")
    A, B = ["a1", "a2"], ["b1", "b2"]
    games = [(f"2026-12-{d:02d}T20:00:00", A, B, "A") for d in range(1, 9)]
    full = elo.compute_full(rows_for(games), now="2026-12-20T00:00:00")
    top = full["players"]["a1"]["elo"]
    assert full["players"]["a1"]["placed"] and full["season_final"][1]["a1"]["elo"] == top
    # a new season: pulled halfway back to 1500, re-places after SEASON_PLACEMENT games
    later = elo.compute_full(rows_for(games), now="2027-01-03T00:00:00")
    a1 = later["players"]["a1"]
    assert abs(a1["elo"] - (1500 + (top - 1500) * elo.SEASON_CARRY)) <= 1
    assert not a1["placed"] and "Season placement" in a1["rank_name"]
    more = games + [(f"2027-01-0{d}T20:00:00", A, B, "B") for d in (2, 3)]
    after = elo.compute_full(rows_for(more), now="2027-01-04T00:00:00")
    assert after["players"]["a1"]["placed"] and after["players"]["a1"]["season_matches"] == 2
    # the upset was flagged by pre-match odds
    assert after["match_odds"][9] > 0.5


def test_prediction_odds_and_ranges():
    A, B = ["a1", "a2"], ["b1", "b2"]
    games = [(f"2026-11-0{d}T20:00:00", A, B, "A") for d in range(1, 6)]
    full = elo.compute_full(rows_for(games), now="2026-11-06T00:00:00")
    pr = elo.predict(full, A, B)
    assert pr["win_prob_a"] > 0.6 and abs(pr["win_prob_a"] + pr["win_prob_b"] - 1) < 1e-9
    a1, b1 = pr["players"]["a1"], pr["players"]["b1"]
    assert 0 < a1["elo_win"][0] <= a1["elo_win"][1] and a1["elo_loss"][0] <= a1["elo_loss"][1] < 0
    # favourites gain less for winning than underdogs
    assert a1["elo_win"][1] < b1["elo_win"][1]
    new = elo.predict(full, ["a1", "zz"], ["b1"])
    assert new["players"]["zz"]["elo_matches"] == 0
