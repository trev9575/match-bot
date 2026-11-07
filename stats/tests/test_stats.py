import os
import sys
import types

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cs2stats.model import MatchData, Round, Kill, Damage, Blind, T, CT, TICKRATE
from cs2stats.stats import compute

A1, A2, A3, B1, B2, B3 = "1", "2", "3", "11", "12", "13"
NAMES = {s: f"p{s}" for s in (A1, A2, A3, B1, B2, B3)}


def roster(a_side=T):
    b_side = CT if a_side == T else T
    return {A1: a_side, A2: a_side, A3: a_side, B1: b_side, B2: b_side, B3: b_side}


def test_trade_kast_entry_and_damage_cap():
    r = Round(1, winner=T, start_tick=0, roster=roster(),
              kills=[Kill(100, B1, A1),                       # opening: B1 kills A1
                     Kill(100 + 3 * TICKRATE, A2, B1),        # A2 trades within 5s
                     Kill(2000, A2, B2, assister=A3),
                     Kill(2100, A3, B3, headshot=True)],
              damages=[Damage(90, B1, A1, 150),               # overkill capped to 100
                       Damage(300, A2, B1, 100), Damage(1990, A3, B2, 40), Damage(2000, A2, B2, 60),
                       Damage(2100, A3, B3, 100)])
    P, s = compute(MatchData("de_test", "x.dem", [r], NAMES))
    assert s["score_a"] == 1 and s["score_b"] == 0
    assert P[B1].damage == 100 and P[B1].entry_wins == 1 and P[A1].entry_attempts == 1
    assert P[A2].trade_kills == 1 and P[A1].traded_deaths == 1
    assert P[A1].kast_rounds == 1            # died but was traded
    assert P[A3].assists == 1 and P[A3].hs_kills == 1
    assert P[A2].k2 == 1
    assert P[B2].kast_rounds == 0


def test_late_trade_not_counted():
    r = Round(1, winner=CT, start_tick=0, roster=roster(),
              kills=[Kill(100, B1, A1), Kill(100 + 6 * TICKRATE, A2, B1)])
    P, _ = compute(MatchData("de_test", "x.dem", [r], NAMES))
    assert P[A2].trade_kills == 0 and P[A1].traded_deaths == 0


def test_clutch_and_halftime_team_identity():
    r1 = Round(1, winner=T, start_tick=0, roster=roster(T),
               kills=[Kill(10, B1, A1), Kill(20, B2, A2),     # A3 alone vs 3
                      Kill(30, A3, B1), Kill(40, A3, B2), Kill(50, A3, B3)])
    # second half: team A now on CT, team B wins
    r2 = Round(2, winner=T, start_tick=100, roster=roster(CT), kills=[Kill(110, B1, A1)])
    P, s = compute(MatchData("de_test", "x.dem", [r1, r2], NAMES))
    assert P[A3].clutch_wins == 1 and P[A3].clutches == ["1v3 W"] and P[A3].k3 == 1
    assert P[A1].team == P[A3].team == "A" and P[B1].team == "B"
    assert s["score_a"] == 1 and s["score_b"] == 1
    assert P[A1].result == "T"


def test_teamkill_flash_and_knife_round_ignored():
    knife = Round(1, winner=T, start_tick=0, roster=roster(),
                  kills=[Kill(5, A1, B1, weapon="knife_karambit")])
    r = Round(2, winner=CT, start_tick=100, roster=roster(),
              kills=[Kill(110, A1, A2, weapon="ak47"), Kill(120, B1, A3, weapon="ak47", flash_assister=B2)],
              blinds=[Blind(115, B2, A3, 2.5), Blind(116, B2, B3, 3.0), Blind(117, B2, A1, 0.4)],
              damages=[Damage(118, B3, A1, 20)])
    P, s = compute(MatchData("de_test", "x.dem", [knife, r], NAMES))
    assert s["rounds"] == 1
    assert P[A1].kills == 0 and P[A1].teamkills == 1 and P[A2].deaths == 1
    assert P[B2].flash_assists == 1 and P[B2].assists == 1
    assert P[B2].enemies_flashed == 1 and P[B2].team_flashed == 1   # 0.4s blind ignored


def test_parse_demo_with_fake_parser(monkeypatch, tmp_path):
    """Exercise parse_demo's handling of demoparser2's DataFrame shapes."""
    ent = lambda sid: str(76561198000000000 + int(sid))
    roster_rows = []
    for tick, a_side in ((1008, 2), (3008, 3)):
        for s in (A1, A2, A3):
            roster_rows.append({"tick": tick, "steamid": ent(s), "name": f"p{s}", "team_num": a_side,
                                "current_equip_value": 4200})
        for s in (B1, B2, B3):
            roster_rows.append({"tick": tick, "steamid": ent(s), "name": f"p{s}", "team_num": 5 - a_side,
                                "current_equip_value": 1000})
    events = {
        "round_end": pd.DataFrame([
            {"tick": 50, "winner": "T", "is_warmup_period": True, "total_rounds_played": 0},
            {"tick": 2000, "winner": "T", "is_warmup_period": False, "total_rounds_played": 0},
            {"tick": 4000, "winner": "CT", "is_warmup_period": False, "total_rounds_played": 1}]),
        "round_freeze_end": pd.DataFrame([{"tick": 1000, "is_warmup_period": False},
                                          {"tick": 3000, "is_warmup_period": False}]),
        "player_death": pd.DataFrame([
            {"tick": 1500, "attacker_steamid": ent(A1), "attacker_name": "p1", "user_steamid": ent(B1),
             "user_name": "p11", "assister_steamid": None, "assistedflash": False, "headshot": True,
             "weapon": "ak47", "is_warmup_period": False},
            {"tick": 2100, "attacker_steamid": ent(B2), "attacker_name": "p12", "user_steamid": ent(A2),
             "user_name": "p2", "assister_steamid": ent(B3), "assistedflash": True, "headshot": False,
             "weapon": "m4a1", "is_warmup_period": False}]),   # post-round kill -> round 1
        "player_hurt": pd.DataFrame([{"tick": 1500, "attacker_steamid": ent(A1), "user_steamid": ent(B1),
                                      "dmg_health": 120, "weapon": "ak47", "is_warmup_period": False}]),
        "bomb_planted": pd.DataFrame([{"tick": 1400, "user_steamid": ent(A1), "is_warmup_period": False}]),
        "player_blind": pd.DataFrame([{"tick": 2050, "attacker_steamid": ent(B3), "user_steamid": ent(A2),
                                       "blind_duration": 2.0, "is_warmup_period": False}]),
    }

    class FakeParser:
        def __init__(self, path): pass
        def parse_header(self): return {"map_name": "de_mirage"}
        def parse_event(self, name, *, player=None, other=None): return events.get(name, pd.DataFrame())
        def parse_ticks(self, props, *, players=None, ticks=None): return pd.DataFrame(roster_rows)
        def parse_player_info(self): return pd.DataFrame()

    monkeypatch.setitem(sys.modules, "demoparser2", types.SimpleNamespace(DemoParser=FakeParser))
    from cs2stats.parse_demo import parse_demo
    f = tmp_path / "2026-11-04_20-20-59_3_de_mirage_team_p1_vs_team_p11.dem"
    f.write_bytes(b"")
    md = parse_demo(str(f))
    assert md.matchzy_id == "3" and md.played_at.startswith("2026-11-04T20:20")
    assert len(md.rounds) == 2 and [r.winner for r in md.rounds] == [T, CT]
    assert len(md.rounds[0].kills) == 2 and md.rounds[0].kills[1].flash_assister == ent(B3)
    P, s = compute(md)
    assert P[ent(A1)].damage == 100 and P[ent(A1)].hs_kills == 1
    assert P[ent(B3)].flash_assists == 1 and P[ent(B3)].enemies_flashed == 1
    assert s["score_a"] == 2 and s["score_b"] == 0 and P[ent(A1)].team == "A"  # A won both halves
    r1 = md.rounds[0]
    assert r1.end_tick == 2000 and r1.planter == ent(A1) and r1.loadout[ent(A1)] == 1.0
    assert P[ent(A1)].e_kills < 1.0          # rifle killing a pistol player = eco frag
    assert P[ent(A1)].swing > 0 and P[ent(B1)].swing < 0

    from cs2stats.ingest import team_names
    assert team_names(md.source_name, P) == ("Team p1", "Team p11")


def test_round_swing_rules():
    from cs2stats.rating3 import round_swing, p_ct_wins
    ro = {s: T for s in ("1", "2", "3", "4", "5")} | {s: CT for s in ("11", "12", "13", "14", "15")}
    en = lambda a, v: a in ro and v in ro and ro[a] != ro[v]
    full = {s: 1.0 for s in ro}
    # calibration points
    assert 0.6 < p_ct_wins(5, 4, 1, 1, False) < 0.75 and p_ct_wins(1, 3, 1, 1, False) < 0.12
    # T clutch: "5" survives 1v2 and wins; also an exit frag after the round ends
    kills = [Kill(10, "11", "1"), Kill(20, "12", "2"), Kill(30, "13", "3"), Kill(40, "14", "4"),
             Kill(50, "5", "11"), Kill(60, "5", "12"), Kill(70, "5", "13"), Kill(80, "5", "14"),
             Kill(90, "5", "15"), Kill(200, "5", "1")]
    r = Round(1, winner=T, start_tick=0, roster=ro, kills=kills, end_tick=95)
    sw = round_swing(r, ro, full, en)
    assert abs(sum(sw.values())) < 1e-9                     # zero-sum
    assert sw["5"] == max(sw.values()) and sw["5"] > 0.8     # the clutch dominates
    r2 = Round(1, winner=T, start_tick=0, roster=ro, kills=kills[:9], end_tick=95)
    assert abs(round_swing(r2, ro, full, en)["5"] - sw["5"]) < 1e-9   # exit frag adds nothing


def test_eco_frags_count_less():
    ro = {"1": T, "2": T, "11": CT, "12": CT}
    names = {s: s for s in ro}
    eco = Round(1, winner=T, start_tick=0, roster=ro, loadout={"1": 1.0, "2": 1.0, "11": 0.0, "12": 0.0},
                kills=[Kill(10, "1", "11"), Kill(20, "1", "12")])
    anti = Round(1, winner=CT, start_tick=0, roster=ro, loadout={"1": 0.0, "2": 0.0, "11": 1.0, "12": 1.0},
                 kills=[Kill(10, "11", "1"), Kill(20, "11", "2")])
    up = Round(1, winner=T, start_tick=0, roster=ro, loadout={"1": 0.0, "2": 0.0, "11": 1.0, "12": 1.0},
               kills=[Kill(10, "1", "11"), Kill(20, "1", "12")])
    P1, _ = compute(MatchData("m", "x", [eco], names))
    P2, _ = compute(MatchData("m", "x", [up], names))
    assert P1["1"].e_kills < 2 < P2["1"].e_kills             # pistol 2k on rifles > rifle 2k on pistols
    assert P2["1"].swing > P1["1"].swing
