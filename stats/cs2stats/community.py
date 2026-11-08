"""Community features computed from match history: achievements, duos, rivalries,
head-to-head, weekly recaps and season standings.

All pure functions over rows the DB already returns, so everything stays consistent when a
match is deleted or an old demo is re-read. Nothing here is stored.
"""
from __future__ import annotations

import re
from collections import defaultdict
from itertools import combinations

from . import elo as elo_mod

# ----------------------------------------------------------------------------- achievements
# (id, name, description, tier, icon). Tier sets the badge color: bronze, silver, gold, elite, shame.
# Icons are drawn in icons.py (original artwork, no emoji).
ACHIEVEMENTS = [
    # single-match feats
    ("ace", "Ace", "Kill the entire enemy team in one round", "gold", "skull"),
    ("quad", "Four Piece", "Get four kills in one round", "silver", "four"),
    ("clutch2", "Clutch Minister", "Win a 1v2 clutch", "bronze", "hourglass"),
    ("clutch3", "Ice Cold", "Win a 1v3 clutch", "silver", "snowflake"),
    ("clutch4", "Houdini", "Win a 1v4 (or bigger) clutch", "elite", "keyhole"),
    ("rampage", "Rampage", "30 or more kills in one map", "gold", "flame"),
    ("adr150", "Damage Dealer", "150+ ADR in a full match", "gold", "burst"),
    ("headhunter", "Headhunter", "70%+ headshots with at least 15 kills", "silver", "crosshair"),
    ("door", "Door Kicker", "Win 6+ opening duels in one map", "silver", "door"),
    ("lightsout", "Lights Out", "Blind 15+ enemies in one map", "silver", "bolt"),
    ("nades", "Nade Merchant", "300+ utility damage in one map", "silver", "grenade"),
    ("teamplayer", "Team Player", "10+ assists in one map", "bronze", "people"),
    ("knife", "Cold Steel", "Get a knife kill", "silver", "knife"),
    ("sniper", "Sniper's Nest", "15+ AWP kills in one map", "gold", "scope"),
    ("everpresent", "Ever Present", "90%+ KAST over a full match", "silver", "shield"),
    ("carry", "Carry Job", "1.80+ Rating 3.0 in a map", "gold", "dumbbell"),
    ("mvp", "Match MVP", "Top rating in a match", "bronze", "star"),
    ("steamroll", "Steamroller", "Win a map by 10+ rounds", "bronze", "roller"),
    ("comeback", "Comeback Kid", "Win after trailing by 6+ rounds", "gold", "arrowup"),
    ("marathon", "Marathon", "Win a map that went to overtime", "silver", "clock"),
    ("giantslayer", "Giant Slayer", "Win with under a 35% predicted chance", "gold", "sling"),
    ("defuser", "Wire Cutter", "Defuse the bomb 3 times in one map", "silver", "scissors"),
    ("planter", "Bomb Squad", "Plant the bomb 5 times in one map", "bronze", "bomb"),
    # milestones
    ("placed", "Placed", "Finish your placement matches", "bronze", "chevron"),
    ("vet10", "Regular", "Play 10 matches", "bronze", "medal"),
    ("vet50", "Veteran", "Play 50 matches", "silver", "medal"),
    ("vet100", "Lifer", "Play 100 matches", "gold", "medal"),
    ("kills500", "Body Count", "500 career kills", "silver", "tally"),
    ("kills1000", "Thousand Club", "1,000 career kills", "gold", "tally"),
    ("streak5", "On Fire", "Win 5 matches in a row", "gold", "streak"),
    ("mvp5", "Star Player", "Be match MVP 5 times", "gold", "stars"),
    ("worldtour", "World Tour", "Play on 6 different maps", "bronze", "globe"),
    ("gn", "Gold Nova", "Reach Gold Nova Master", "bronze", "wing1"),
    ("mg", "Guardian", "Reach Master Guardian Elite", "silver", "wing2"),
    ("le", "Eagle", "Reach Legendary Eagle", "gold", "eagle"),
    ("ge", "Global Elite", "Reach The Global Elite", "elite", "globe2"),
    # seasons
    ("champion", "Season Champion", "Finish a season #1 by Elo", "elite", "crown"),
    ("podium", "Podium", "Finish a season in the top 3 by Elo", "gold", "podium"),
    # hall of shame
    ("pacifist", "Pacifist", "Finish a full match with 0 kills", "shame", "flag"),
    ("friendlyfire", "Friendly Flasher", "Blind your own team 8+ times in one map", "shame", "sunglasses"),
]
ACH = {a[0]: {"id": a[0], "name": a[1], "description": a[2], "tier": a[3], "icon": a[4]} for a in ACHIEVEMENTS}
RANK_ACH = [("gn", 10), ("mg", 13), ("le", 15), ("ge", 18)]     # (achievement, minimum rank number)


def _clutch_best(clutches: str) -> int:
    best = 0
    for c in (clutches or "").split(","):
        m = re.match(r"1v(\d+) W", c.strip())
        if m:
            best = max(best, int(m.group(1)))
    return best


def max_deficit(round_log: str, team: str) -> int:
    """Biggest round deficit `team` ("A"/"B") faced during the map."""
    a = b = worst = 0
    for ch in (round_log or "").upper():
        if ch == "A":
            a += 1
        elif ch == "B":
            b += 1
        worst = max(worst, (b - a) if team == "A" else (a - b))
    return worst


def _full(r) -> bool:
    """Played most of the map (not a late sub or early leaver)."""
    return (r.get("rounds") or 0) >= 0.8 * (r.get("match_rounds") or r.get("rounds") or 1)


def match_feats(r: dict, match_rows: list[dict], odds_a=None) -> list[str]:
    """Single-match achievements earned by row r (a player's map)."""
    out = []
    rounds = max(r.get("rounds") or 0, 1)
    kills = r.get("kills") or 0
    won = r.get("result") == "W"
    if r.get("aces") or r.get("k5"):
        out.append("ace")
    if r.get("k4"):
        out.append("quad")
    cb = _clutch_best(r.get("clutches"))
    if cb >= 2:
        out.append("clutch2")
    if cb >= 3:
        out.append("clutch3")
    if cb >= 4:
        out.append("clutch4")
    if kills >= 30:
        out.append("rampage")
    if _full(r) and (r.get("damage") or 0) / rounds >= 150 and rounds >= 13:
        out.append("adr150")
    if kills >= 15 and (r.get("hs_kills") or 0) / kills >= 0.7:
        out.append("headhunter")
    if (r.get("entry_wins") or 0) >= 6:
        out.append("door")
    if (r.get("enemies_flashed") or 0) >= 15:
        out.append("lightsout")
    if (r.get("util_damage") or 0) >= 300:
        out.append("nades")
    if (r.get("assists") or 0) >= 10:
        out.append("teamplayer")
    if r.get("knife_kills"):
        out.append("knife")
    if (r.get("awp_kills") or 0) >= 15:
        out.append("sniper")
    if _full(r) and rounds >= 13 and (r.get("kast_rounds") or 0) / rounds >= 0.9:
        out.append("everpresent")
    if r.get("rating") is not None and r["rating"] >= 1.8:
        out.append("carry")
    rated = [x for x in match_rows if x.get("rating") is not None]
    if rated:
        top = max(rated, key=lambda x: (x["rating"], x.get("kills") or 0))
        if top["steamid"] == r["steamid"]:
            out.append("mvp")
    if won:
        mine, theirs = ((r.get("score_a"), r.get("score_b")) if r.get("team") == "A"
                        else (r.get("score_b"), r.get("score_a")))
        if (mine or 0) - (theirs or 0) >= 10:
            out.append("steamroll")
        if max_deficit(r.get("round_log"), r.get("team")) >= 6:
            out.append("comeback")
        if (r.get("match_rounds") or 0) > 24:
            out.append("marathon")
        if odds_a is not None:
            mine_p = odds_a if r.get("team") == "A" else 1 - odds_a
            if mine_p < 0.35:
                out.append("giantslayer")
    if (r.get("defuses") or 0) >= 3:
        out.append("defuser")
    if (r.get("plants") or 0) >= 5:
        out.append("planter")
    if _full(r) and rounds >= 13 and kills == 0:
        out.append("pacifist")
    if (r.get("team_flashed") or 0) >= 8:
        out.append("friendlyfire")
    return out


def achievements(rows: list[dict], full_elo: dict) -> dict:
    """rows: every player-map (DB.all_rows), oldest first.

    Returns {"players": {sid: {ach_id: {count, first_match, first_at, last_match}}},
             "match_unlocks": {match_id: [(sid, ach_id), ...]}  (first-time unlocks only)}
    """
    by_match = defaultdict(list)
    for r in rows:
        by_match[r["match_id"]].append(r)
    odds = full_elo.get("match_odds", {})
    per_match_elo = full_elo.get("per_match", {})
    got: dict[str, dict] = defaultdict(dict)
    unlocks = defaultdict(list)
    run = defaultdict(lambda: {"matches": 0, "kills": 0, "streak": 0, "mvp": 0, "maps": set()})

    def give(sid, aid, r):
        e = got[sid].get(aid)
        if e:
            e["count"] += 1
            e["last_match"] = r["match_id"] if r else e["last_match"]
        else:
            got[sid][aid] = {"count": 1, "first_match": r["match_id"] if r else None,
                             "first_at": r["played_at"] if r else None,
                             "last_match": r["match_id"] if r else None}
            if r:
                unlocks[r["match_id"]].append((sid, aid))

    order = sorted(by_match, key=lambda m: (by_match[m][0].get("played_at") or "", m))
    for mid in order:
        mrows = by_match[mid]
        for r in mrows:
            sid = r["steamid"]
            feats = match_feats(r, mrows, odds.get(mid))
            for aid in feats:
                give(sid, aid, r)
            s = run[sid]
            s["matches"] += 1
            s["kills"] += r.get("kills") or 0
            s["streak"] = s["streak"] + 1 if r.get("result") == "W" else 0
            s["mvp"] += "mvp" in feats
            if r.get("map"):
                s["maps"].add(r["map"])
            have = got[sid]
            checks = [("placed", s["matches"] >= elo_mod.PLACEMENT_MATCHES),
                      ("vet10", s["matches"] >= 10), ("vet50", s["matches"] >= 50),
                      ("vet100", s["matches"] >= 100),
                      ("kills500", s["kills"] >= 500), ("kills1000", s["kills"] >= 1000),
                      ("streak5", s["streak"] >= 5), ("mvp5", s["mvp"] >= 5),
                      ("worldtour", len(s["maps"]) >= 6)]
            e = per_match_elo.get((mid, sid))
            if e and s["matches"] >= elo_mod.PLACEMENT_MATCHES:
                rank = elo_mod.rank_for(e["elo_after"])["rank"]
                checks += [(aid, rank >= need) for aid, need in RANK_ACH]
            for aid, ok in checks:
                if ok and aid not in have:
                    give(sid, aid, r)
                elif ok and aid == "streak5" and s["streak"] % 5 == 0:
                    give(sid, aid, r)                      # every 5-win streak counts again
    # finished seasons
    for season, standings in season_standings(full_elo).items():
        if not standings["finished"]:
            continue
        for i, p in enumerate(standings["players"][:3]):
            give(p["steamid"], "podium", None)
            got[p["steamid"]]["podium"]["first_at"] = got[p["steamid"]]["podium"]["first_at"] or standings["end"]
            if i == 0:
                give(p["steamid"], "champion", None)
                got[p["steamid"]]["champion"]["first_at"] = (got[p["steamid"]]["champion"]["first_at"]
                                                             or standings["end"])
    return {"players": dict(got), "match_unlocks": dict(unlocks)}


def achievement_catalog(result: dict, player_count: int) -> list[dict]:
    """Every achievement with how many players have it."""
    holders = defaultdict(int)
    for a in result["players"].values():
        for aid in a:
            holders[aid] += 1
    out = []
    for aid, info in ACH.items():
        n = holders.get(aid, 0)
        out.append({**info, "holders": n, "rarity": round(100 * n / player_count, 1) if player_count else 0})
    return out


def player_achievements(result: dict, sid: str, player_count: int) -> list[dict]:
    mine = result["players"].get(sid, {})
    cat = {a["id"]: a for a in achievement_catalog(result, player_count)}
    out = []
    for aid in ACH:
        e = mine.get(aid)
        out.append({**cat[aid], "unlocked": bool(e), **(e or {"count": 0})})
    return out


# ----------------------------------------------------------------------------- seasons

def season_standings(full_elo: dict) -> dict:
    """season -> {name, start, end, finished, players: [final Elo standings]}"""
    out = {}
    now = full_elo.get("now")
    for season, snap in sorted(full_elo.get("season_final", {}).items()):
        info = elo_mod.season_info(season, now)
        ps = [{"steamid": sid, **v} for sid, v in snap.items()
              if v["placed"] and v["season_matches"] >= max(elo_mod.SEASON_PLACEMENT, 1)]
        ps.sort(key=lambda p: -p["elo"])
        out[season] = {**info, "players": ps}
    return out


# ----------------------------------------------------------------------------- duos & rivals

def _teams_by_match(rows):
    by = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r.get("team") in ("A", "B"):
            by[r["match_id"]][r["team"]].append(r)
    return by


def duos(rows: list[dict]) -> dict:
    """(sid1, sid2) sorted -> {games, wins, rating_sum, rated}"""
    out = defaultdict(lambda: {"games": 0, "wins": 0, "rating_sum": 0.0, "rated": 0})
    for teams in _teams_by_match(rows).values():
        for team in teams.values():
            for a, b in combinations(sorted(team, key=lambda r: r["steamid"]), 2):
                d = out[(a["steamid"], b["steamid"])]
                d["games"] += 1
                d["wins"] += a.get("result") == "W"
                if a.get("rating") is not None and b.get("rating") is not None:
                    d["rating_sum"] += (a["rating"] + b["rating"]) / 2
                    d["rated"] += 1
    return dict(out)


def opponents(rows: list[dict]) -> dict:
    """(me, opp) -> {games, wins} for every pair who were on opposite teams."""
    out = defaultdict(lambda: {"games": 0, "wins": 0})
    for teams in _teams_by_match(rows).values():
        for x in teams.get("A", []):
            for y in teams.get("B", []):
                for me, opp in ((x, y), (y, x)):
                    o = out[(me["steamid"], opp["steamid"])]
                    o["games"] += 1
                    o["wins"] += me.get("result") == "W"
    return dict(out)


def pair_totals(kill_pairs: list[dict]) -> dict:
    """(killer, victim) -> {kills, hs, openings} summed over matches."""
    out = defaultdict(lambda: {"kills": 0, "hs": 0, "openings": 0})
    for k in kill_pairs:
        t = out[(k["killer"], k["victim"])]
        t["kills"] += k["kills"] or 0
        t["hs"] += k["hs"] or 0
        t["openings"] += k["openings"] or 0
    return dict(out)


def superlatives(rows, kill_pairs, names: dict, min_duo=3, min_rival=3) -> dict:
    """Best/worst/most-played duo, biggest rivalry, most one-sided matchup."""
    def nm(s):
        return names.get(s, s)

    ds = [{"players": [{"steamid": a, "name": nm(a)}, {"steamid": b, "name": nm(b)}],
           "games": d["games"], "wins": d["wins"], "win_pct": 100 * d["wins"] / d["games"],
           "rating": (d["rating_sum"] / d["rated"]) if d["rated"] else None}
          for (a, b), d in duos(rows).items()]
    qual = [d for d in ds if d["games"] >= min_duo]
    opp = opponents(rows)
    pt = pair_totals(kill_pairs)
    rivals = []
    seen = set()
    for (a, b) in pt:
        key = tuple(sorted((a, b)))
        if key in seen:
            continue
        seen.add(key)
        ab, ba = pt.get((key[0], key[1]), {}).get("kills", 0), pt.get((key[1], key[0]), {}).get("kills", 0)
        games = opp.get(key, {}).get("games", 0)
        if games < min_rival:
            continue
        rivals.append({"players": [{"steamid": key[0], "name": nm(key[0]), "kills": ab,
                                    "wins": opp.get((key[0], key[1]), {}).get("wins", 0)},
                                   {"steamid": key[1], "name": nm(key[1]), "kills": ba,
                                    "wins": opp.get((key[1], key[0]), {}).get("wins", 0)}],
                       "games": games, "total": ab + ba, "gap": abs(ab - ba)})
    out = {
        "best_duo": max(qual, key=lambda d: (d["win_pct"], d["games"]), default=None),
        "worst_duo": min(qual, key=lambda d: (d["win_pct"], -d["games"]), default=None),
        "most_played_duo": max(ds, key=lambda d: (d["games"], d["win_pct"]), default=None),
        "best_rated_duo": max((d for d in qual if d["rating"] is not None),
                              key=lambda d: d["rating"], default=None),
        "rivalry": max(rivals, key=lambda r: (r["total"] / max(r["games"], 1), r["games"]), default=None),
        "one_sided": max((r for r in rivals if r["total"] >= 10),
                         key=lambda r: (r["gap"] / r["total"], r["total"]), default=None),
        "duos": sorted(qual, key=lambda d: (-d["win_pct"], -d["games"]))[:15],
        "rivalries": sorted(rivals, key=lambda r: -r["total"])[:15],
    }
    return out


def head_to_head(sid, rows, kill_pairs, names: dict) -> dict:
    """A player's record against and with everyone else."""
    pt = pair_totals(kill_pairs)
    opp = opponents(rows)
    ds = duos(rows)
    others = {s for pair in list(pt) + list(opp) for s in pair if s != sid and sid in pair}
    vs = []
    for o in others:
        k, d = pt.get((sid, o), {}), pt.get((o, sid), {})
        g = opp.get((sid, o), {"games": 0, "wins": 0})
        if not (g["games"] or k.get("kills") or d.get("kills")):
            continue
        vs.append({"steamid": o, "name": names.get(o, o), "games": g["games"], "wins": g["wins"],
                   "kills": k.get("kills", 0), "deaths": d.get("kills", 0), "hs": k.get("hs", 0),
                   "openings_won": k.get("openings", 0), "openings_lost": d.get("openings", 0)})
    vs.sort(key=lambda x: (-(x["kills"] + x["deaths"]), -x["games"]))
    mates = []
    for (a, b), d in ds.items():
        if sid in (a, b):
            o = b if a == sid else a
            mates.append({"steamid": o, "name": names.get(o, o), "games": d["games"], "wins": d["wins"],
                          "win_pct": 100 * d["wins"] / d["games"],
                          "rating": (d["rating_sum"] / d["rated"]) if d["rated"] else None})
    mates.sort(key=lambda x: (-x["games"], -x["win_pct"]))
    with_kills = [v for v in vs if v["deaths"]]
    nemesis = max(with_kills, key=lambda v: (v["deaths"] - v["kills"], v["deaths"]), default=None)
    victim = max((v for v in vs if v["kills"]), key=lambda v: (v["kills"] - v["deaths"], v["kills"]), default=None)
    if nemesis and nemesis["deaths"] <= nemesis["kills"]:
        nemesis = None
    if victim and victim["kills"] <= victim["deaths"]:
        victim = None
    return {"opponents": vs, "teammates": mates, "nemesis": nemesis, "favorite_victim": victim}


def openings(sid, rows) -> dict:
    """Opening duels: overall and by side (side split exists for demos read by the newer parser)."""
    mine = [r for r in rows if r["steamid"] == sid]
    tot = lambda k: sum(r.get(k) or 0 for r in mine)
    att, win = tot("entry_attempts"), tot("entry_wins")
    rounds = max(tot("rounds"), 1)
    out = {"attempts": att, "wins": win, "losses": att - win,
           "win_pct": 100 * win / att if att else None, "attempt_rate": 100 * att / rounds}
    for sd in ("t", "ct"):
        a, w = tot(f"entry_attempts_{sd}"), tot(f"entry_wins_{sd}")
        out[sd] = {"attempts": a, "wins": w, "win_pct": 100 * w / a if a else None}
    # match win rate in maps where this player won 3+ opening duels ("do my entries matter")
    big = [r for r in mine if (r.get("entry_wins") or 0) >= 3]
    out["win_pct_3plus_openers"] = 100 * sum(r.get("result") == "W" for r in big) / len(big) if big else None
    return out


# ----------------------------------------------------------------------------- recap

def recap(rows, kill_pairs, full_elo, ach_result, names, since, until) -> dict:
    """What happened between since and until (ISO strings)."""
    inside = [r for r in rows if since <= (r.get("played_at") or "") < until]
    mids = sorted({r["match_id"] for r in inside})
    by_player = defaultdict(list)
    for r in inside:
        by_player[r["steamid"]].append(r)
    per = full_elo.get("per_match", {})
    players = full_elo.get("players", {})

    def nm(s):
        return names.get(s, s)

    summary = []
    for sid, rs in by_player.items():
        rated = [r["rating"] for r in rs if r.get("rating") is not None]
        rnds = sum(r.get("rounds") or 0 for r in rs)
        summary.append({
            "steamid": sid, "name": nm(sid), "maps": len(rs),
            "wins": sum(r.get("result") == "W" for r in rs),
            "kills": sum(r.get("kills") or 0 for r in rs), "deaths": sum(r.get("deaths") or 0 for r in rs),
            "adr": sum(r.get("damage") or 0 for r in rs) / max(rnds, 1),
            "rating": sum(rated) / len(rated) if rated else None,
            "elo_change": (sum(per.get((r["match_id"], sid), {}).get("elo_delta", 0) for r in rs)
                           if players.get(sid, {}).get("placed") else None),
            "aces": sum(r.get("aces") or 0 for r in rs),
            "clutches": sum(r.get("clutch_wins") or 0 for r in rs),
            "openings": sum(r.get("entry_wins") or 0 for r in rs),
        })
    regulars = [s for s in summary if s["maps"] >= 2] or summary
    best_game = max((r for r in inside if r.get("rating") is not None), key=lambda r: r["rating"], default=None)
    odds = full_elo.get("match_odds", {})
    upset = None
    by_match = defaultdict(list)
    for r in inside:
        by_match[r["match_id"]].append(r)
    for mid, rs in by_match.items():
        if mid not in odds:
            continue
        winner = next((r["team"] for r in rs if r.get("result") == "W"), None)
        if not winner:
            continue
        p = odds[mid] if winner == "A" else 1 - odds[mid]
        if upset is None or p < upset["chance"]:
            upset = {"match_id": mid, "map": rs[0].get("map"), "chance": p,
                     "winners": [nm(r["steamid"]) for r in rs if r["team"] == winner],
                     "score": f"{rs[0].get('score_a')}-{rs[0].get('score_b')}"}
    sup = superlatives(inside, [k for k in kill_pairs if k["match_id"] in set(mids)], names, min_duo=2, min_rival=2)
    unlocked = []
    for mid in mids:
        for sid, aid in ach_result.get("match_unlocks", {}).get(mid, []):
            if aid not in ("mvp",):
                unlocked.append({**ACH[aid], "steamid": sid, "player": nm(sid)})

    def top(key, pool=None, rev=True):
        pool = [s for s in (pool or summary) if s.get(key) is not None]
        return max(pool, key=lambda s: s[key]) if rev and pool else (min(pool, key=lambda s: s[key]) if pool else None)

    return {
        "since": since, "until": until, "matches": len(mids),
        "rounds": sum(by_match[m][0].get("match_rounds") or 0 for m in mids),
        "players": len(summary),
        "top_rating": top("rating", regulars),
        "top_kills": top("kills"),
        "top_elo": top("elo_change"),
        "worst_elo": top("elo_change", rev=False),
        "most_maps": top("maps"),
        "most_aces": top("aces") if any(s["aces"] for s in summary) else None,
        "most_clutches": top("clutches") if any(s["clutches"] for s in summary) else None,
        "best_game": {"steamid": best_game["steamid"], "name": nm(best_game["steamid"]),
                      "rating": best_game["rating"], "kills": best_game.get("kills"),
                      "deaths": best_game.get("deaths"), "map": best_game.get("map"),
                      "match_id": best_game["match_id"]} if best_game else None,
        "upset": upset if upset and upset["chance"] < 0.45 else None,
        "best_duo": sup["best_duo"], "rivalry": sup["rivalry"],
        "achievements": unlocked[:25],
        "table": sorted(summary, key=lambda s: (-(s["rating"] or 0), -s["kills"])),
    }
