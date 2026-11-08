"""Skill rating (Elo) modeled on CS:GO competitive matchmaking.

Valve said CS:GO's ranks ran on a Glicko-2 based system: only the result of the
match (win / loss / tie) moves your rating, a rating that's still uncertain moves
more, and uncertainty grows again while you don't play. This module follows
Glickman's Glicko-2 (http://www.glicko.net/glicko/glicko2.pdf) with one match as
one rating period, and treats the opposing team as a single opponent whose rating
is the average of its players.

What Valve never published, and is modeled here instead: where each of the 18
ranks starts, and the exact team/opponent treatment. Our bands put the 1500
starting rating at Gold Nova III, where a new CS:GO account typically placed.

Everything is recomputed from the match history (a few hundred rows), so deleting
a bad match or importing an old demo always leaves consistent numbers, and
nothing extra is stored.
"""
from __future__ import annotations

import math
import os
from datetime import datetime

START, START_RD, START_VOL = 1500.0, 350.0, 0.06
TAU = 0.5                      # how fast volatility can change (Glickman: 0.3-1.2)
SCALE = 173.7178
MIN_RD = 45.0                  # floor, so a veteran's rating can still move
IDLE_PERIOD_DAYS = 7           # one Glicko "rating period" of inactivity per week idle


def env_int(name, default):
    v = os.environ.get(name, "").split("#")[0].strip()
    return int(v) if v else default


PLACEMENT_MATCHES = env_int("PLACEMENT_MATCHES", 5)   # CS:GO needed 10 wins; ours is matches

# Win contribution: how much a player's own Rating 3.0 in a match scales their Elo change.
# multiplier = 1 + weight * (rating - 1) on a win, 1 - weight * (rating - 1) on a loss, capped to
# 1 +/- MAX_ADJUST. Default: a 1.50 game in a win earns 20% more, a 0.50 game in a loss costs 20%
# more; never more than 30% either way. CONTRIBUTION_WEIGHT=0 turns it off (pure CS:GO style).
CONTRIBUTION_WEIGHT = float(os.environ.get("CONTRIBUTION_WEIGHT", "0.4").split("#")[0].strip() or 0)
MAX_ADJUST = 0.30


def contribution(rating, score) -> float:
    if rating is None or not CONTRIBUTION_WEIGHT or score == 0.5:
        return 1.0
    f = 1 + CONTRIBUTION_WEIGHT * (rating - 1) * (1 if score == 1 else -1)
    return max(1 - MAX_ADJUST, min(1 + MAX_ADJUST, f))
RANK_EXPIRE_DAYS = env_int("RANK_EXPIRE_DAYS", 30)

# Seasons: every SEASON_MONTHS months from SEASON_ANCHOR. When a new season starts, Elo is pulled
# halfway back to 1500 (SEASON_CARRY = how much of the distance you keep), uncertainty goes up so
# early games move more, and placed players need SEASON_PLACEMENT games before their rank shows.
SEASON_MONTHS = max(env_int("SEASON_MONTHS", 3), 1)
SEASON_ANCHOR = (os.environ.get("SEASON_ANCHOR", "").split("#")[0].strip() or "2026-10-01")
SEASON_CARRY = float(os.environ.get("SEASON_CARRY", "0.5").split("#")[0].strip() or 0.5)
SEASON_RD = 200.0
SEASON_PLACEMENT = env_int("SEASON_PLACEMENT", 2)


def _anchor():
    a = datetime.fromisoformat(SEASON_ANCHOR[:10])
    return a.year, a.month


def season_of(ts) -> int:
    """Season number for a timestamp (1 = the season starting at SEASON_ANCHOR)."""
    try:
        d = datetime.fromisoformat(str(ts)[:19])
    except (TypeError, ValueError):
        return 1
    y, m = _anchor()
    months = (d.year - y) * 12 + (d.month - m)
    return months // SEASON_MONTHS + 1


def season_bounds(n: int) -> tuple[str, str]:
    y, m = _anchor()
    def at(k):
        total = (y * 12 + m - 1) + k * SEASON_MONTHS
        return f"{total // 12:04d}-{total % 12 + 1:02d}-01T00:00:00"
    return at(n - 1), at(n)


def season_info(n: int, now: str | None = None) -> dict:
    start, end = season_bounds(n)
    cur = season_of(now or datetime.now().isoformat(timespec="seconds"))
    return {"season": n, "name": f"Season {n}", "start": start, "end": end,
            "current": n == cur, "finished": n < cur}    # CS:GO hid your rank after ~a month idle; 0 = never

# (name, short, color, minimum Elo). Colors are our own tier colors.
RANKS = [
    ("Silver I", "S1", "#9aa3ad", -10_000),
    ("Silver II", "S2", "#9aa3ad", 1000),
    ("Silver III", "S3", "#9aa3ad", 1075),
    ("Silver IV", "S4", "#9aa3ad", 1150),
    ("Silver Elite", "SE", "#b8c0c8", 1225),
    ("Silver Elite Master", "SEM", "#c9d0d6", 1300),
    ("Gold Nova I", "GN1", "#d8a93a", 1375),
    ("Gold Nova II", "GN2", "#d8a93a", 1425),
    ("Gold Nova III", "GN3", "#e0b445", 1475),
    ("Gold Nova Master", "GNM", "#ecc35a", 1525),
    ("Master Guardian I", "MG1", "#3fa7d6", 1575),
    ("Master Guardian II", "MG2", "#3fa7d6", 1625),
    ("Master Guardian Elite", "MGE", "#4cb8e8", 1700),
    ("Distinguished Master Guardian", "DMG", "#16171b", 1775),
    ("Legendary Eagle", "LE", "#16171b", 1850),
    ("Legendary Eagle Master", "LEM", "#16171b", 1925),
    ("Supreme Master First Class", "SMFC", "#c62828", 2000),
    ("The Global Elite", "GE", "#e53935", 2100),
]


def rank_for(elo: float) -> dict:
    idx = max(i for i, r in enumerate(RANKS) if elo >= r[3])
    name, short, color, _ = RANKS[idx]
    return {"rank": idx + 1, "rank_name": name, "rank_short": short, "rank_color": color}


def _g(phi):
    return 1 / math.sqrt(1 + 3 * phi * phi / math.pi ** 2)


def _new_vol(phi, sigma, delta, v):
    """Glicko-2 step 5: volatility via the Illinois algorithm."""
    a = math.log(sigma * sigma)

    def f(x):
        ex = math.exp(x)
        return (ex * (delta * delta - phi * phi - v - ex) / (2 * (phi * phi + v + ex) ** 2)) - (x - a) / TAU ** 2

    A = a
    if delta * delta > phi * phi + v:
        B = math.log(delta * delta - phi * phi - v)
    else:
        k = 1
        while f(a - k * TAU) < 0:
            k += 1
        B = a - k * TAU
    fA, fB = f(A), f(B)
    for _ in range(100):
        if abs(B - A) <= 1e-6:
            break
        C = A + (A - B) * fA / (fB - fA)
        fC = f(C)
        if fC * fB <= 0:
            A, fA = B, fB
        else:
            fA /= 2
        B, fB = C, fC
    return math.exp(A / 2)


def glicko2_update(r, rd, vol, games, min_rd=MIN_RD):
    """One rating period. games = [(opp_rating, opp_rd, score), ...]. Returns (r, rd, vol)."""
    mu, phi = (r - START) / SCALE, rd / SCALE
    terms = []
    for opp_r, opp_rd, score in games:
        g = _g(opp_rd / SCALE)
        E = 1 / (1 + math.exp(-g * (mu - (opp_r - START) / SCALE)))
        terms.append((g, E, score))
    v = 1 / sum(g * g * E * (1 - E) for g, E, _ in terms)
    delta = v * sum(g * (s - E) for g, E, s in terms)
    sigma = _new_vol(phi, vol, delta, v)
    phi_star = math.sqrt(phi * phi + sigma * sigma)
    phi_new = 1 / math.sqrt(1 / phi_star ** 2 + 1 / v)
    mu_new = mu + phi_new ** 2 * sum(g * (s - E) for g, E, s in terms)
    return START + SCALE * mu_new, max(SCALE * phi_new, min_rd), sigma


def _idle_rd(rd, vol, days):
    """Rating periods with no games: uncertainty grows (Glicko-2 step 6 only)."""
    periods = days / IDLE_PERIOD_DAYS
    phi = rd / SCALE
    return min(SCALE * math.sqrt(phi * phi + periods * vol * vol), START_RD)


def _days(a, b):
    try:
        return max((datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 86400, 0)
    except (TypeError, ValueError):
        return 0


def win_probability(a: list[tuple], b: list[tuple]) -> float:
    """Chance team A beats team B. a, b = [(elo, rd), ...]."""
    if not a or not b:
        return 0.5
    ra, rb = sum(x[0] for x in a) / len(a), sum(x[0] for x in b) / len(b)
    rda = math.sqrt(sum(x[1] ** 2 for x in a) / len(a))
    rdb = math.sqrt(sum(x[1] ** 2 for x in b) / len(b))
    g = _g(math.sqrt(rda ** 2 + rdb ** 2) / SCALE)
    return 1 / (1 + math.exp(-g * (ra - rb) / SCALE))


def _new_state():
    return {"elo": START, "rd": START_RD, "vol": START_VOL, "matches": 0, "last": None,
            "season": None, "season_matches": 0}


def _season_roll(s, season):
    """Soft reset when a player's first game of a new season comes around."""
    if s["season"] is not None and season > s["season"]:
        s["elo"] = START + (s["elo"] - START) * SEASON_CARRY
        s["rd"] = max(s["rd"], SEASON_RD)
        s["season_matches"] = 0
    if s["season"] is None or season > s["season"]:
        s["season"] = season


def compute(rows: list[dict], now: str | None = None):
    """rows: one per player per match with match_id, played_at, steamid, team, result.

    Returns (players, per_match):
      players[steamid]   = {elo, rd, matches, placed, rank..., last_played, inactive}
      per_match[(match_id, steamid)] = {elo_before, elo_after, elo_delta}
    """
    full = compute_full(rows, now)
    return full["players"], full["per_match"]


def _public(s, now, cur_season):
    """What we show for a player's current state (after any pending season reset)."""
    s = dict(s)
    if s["last"]:
        s["rd"] = _idle_rd(s["rd"], s["vol"], _days(s["last"], now))
    _season_roll(s, cur_season)
    placed = s["matches"] >= PLACEMENT_MATCHES
    # a veteran who placed in an earlier season re-places; players placing for the first time
    # this season count as placed as soon as their lifetime placement is done
    if placed and s["season_matches"] < SEASON_PLACEMENT and s["matches"] - s["season_matches"] >= PLACEMENT_MATCHES:
        placed = False
    inactive = bool(RANK_EXPIRE_DAYS) and bool(s["last"]) and _days(s["last"], now) > RANK_EXPIRE_DAYS
    info = {"elo": round(s["elo"]), "elo_rd": round(s["rd"]), "elo_matches": s["matches"],
            "season_matches": s["season_matches"], "placed": placed, "inactive": inactive,
            "placement_needed": PLACEMENT_MATCHES}
    if placed and not inactive:
        info.update(rank_for(s["elo"]))
    else:
        if s["matches"] < PLACEMENT_MATCHES:
            label = f"Unranked ({s['matches']}/{PLACEMENT_MATCHES})"
        elif inactive:
            label = "Rank expired"
        elif not placed:
            label = f"Season placement ({s['season_matches']}/{SEASON_PLACEMENT})"
        else:
            label = "Rank expired"
        info.update({"rank": 0, "rank_short": "—", "rank_color": "#6d6d6d", "rank_name": label})
    return info, s


def compute_full(rows: list[dict], now: str | None = None) -> dict:
    """Everything compute() does, plus season snapshots, per-match odds and raw state."""
    by_match: dict[int, dict] = {}
    for r in rows:
        m = by_match.setdefault(r["match_id"], {"played_at": r["played_at"] or "", "players": []})
        m["players"].append(r)
    state: dict[str, dict] = {}
    per_match = {}
    match_odds = {}            # match_id -> chance team A had before the match
    season_final = {}          # season -> steamid -> state after their last game of that season
    for mid, m in sorted(by_match.items(), key=lambda kv: (kv[1]["played_at"], kv[0])):
        season = season_of(m["played_at"])
        teams = {"A": [], "B": []}
        for p in m["players"]:
            if p["team"] in teams:
                teams[p["team"]].append(p)
        if not teams["A"] or not teams["B"]:
            continue
        # bring everyone's uncertainty up to date before the match
        for p in m["players"]:
            s = state.setdefault(p["steamid"], _new_state())
            if s["last"]:
                s["rd"] = _idle_rd(s["rd"], s["vol"], _days(s["last"], m["played_at"]))
            _season_roll(s, season)
        avg = {t: (sum(state[p["steamid"]]["elo"] for p in ps) / len(ps),
                   math.sqrt(sum(state[p["steamid"]]["rd"] ** 2 for p in ps) / len(ps)))
               for t, ps in teams.items()}
        match_odds[mid] = win_probability(
            [(state[p["steamid"]]["elo"], state[p["steamid"]]["rd"]) for p in teams["A"]],
            [(state[p["steamid"]]["elo"], state[p["steamid"]]["rd"]) for p in teams["B"]])
        updates = {}
        for t, ps in teams.items():
            opp_r, opp_rd = avg["B" if t == "A" else "A"]
            for p in ps:
                s = state[p["steamid"]]
                score = {"W": 1.0, "L": 0.0}.get(p["result"], 0.5)
                r, rd, vol = glicko2_update(s["elo"], s["rd"], s["vol"], [(opp_r, opp_rd, score)])
                # scale the rating change (not the uncertainty) by this player's contribution
                r = s["elo"] + (r - s["elo"]) * contribution(p.get("rating"), score)
                updates[p["steamid"]] = (r, rd, vol)
        for sid, (r, rd, vol) in updates.items():
            s = state[sid]
            per_match[(mid, sid)] = {"elo_before": round(s["elo"]), "elo_after": round(r),
                                     "elo_delta": round(r) - round(s["elo"])}
            s.update(elo=r, rd=rd, vol=vol, matches=s["matches"] + 1, last=m["played_at"],
                     season_matches=s["season_matches"] + 1)
            season_final.setdefault(season, {})[sid] = {
                "elo": round(r), "matches": s["matches"], "season_matches": s["season_matches"],
                "placed": s["matches"] >= PLACEMENT_MATCHES}

    now = now or datetime.now().isoformat(timespec="seconds")
    cur = season_of(now)
    players = {}
    for sid, s in state.items():
        players[sid], _ = _public(s, now, cur)
    for snap in season_final.values():
        for v in snap.values():
            v.update(rank_for(v["elo"]) if v["placed"] else
                     {"rank": 0, "rank_short": "—", "rank_color": "#6d6d6d", "rank_name": "Unranked"})
    return {"players": players, "per_match": per_match, "match_odds": match_odds,
            "season_final": season_final, "state": state, "now": now, "season": cur}


def contribution_range() -> tuple[float, float]:
    if not CONTRIBUTION_WEIGHT:
        return 1.0, 1.0
    return 1 - MAX_ADJUST, 1 + MAX_ADJUST


def predict(full: dict, team_a: list[str], team_b: list[str]) -> dict:
    """Pre-match odds and each player's possible Elo change, from compute_full() output."""
    now, cur = full["now"], full["season"]
    cur_state = {}
    for sid in set(team_a) | set(team_b):
        s = full["state"].get(sid) or _new_state()
        info, live = _public(s, now, cur)
        cur_state[sid] = (info, live)
    side = {"A": team_a, "B": team_b}
    avg = {t: (sum(cur_state[s][1]["elo"] for s in ids) / len(ids),
               math.sqrt(sum(cur_state[s][1]["rd"] ** 2 for s in ids) / len(ids))) if ids else (START, START_RD)
           for t, ids in side.items()}
    pa = win_probability([(cur_state[s][1]["elo"], cur_state[s][1]["rd"]) for s in team_a],
                         [(cur_state[s][1]["elo"], cur_state[s][1]["rd"]) for s in team_b])
    lo, hi = contribution_range()
    players = {}
    for t, ids in side.items():
        opp = avg["B" if t == "A" else "A"]
        for sid in ids:
            info, live = cur_state[sid]
            out = {}
            for label, score in (("win", 1.0), ("loss", 0.0)):
                r, _, _ = glicko2_update(live["elo"], live["rd"], live["vol"], [(opp[0], opp[1], score)])
                d = r - live["elo"]
                a, b = sorted((d * lo, d * hi))
                out[label] = [round(a), round(b)]
            players[sid] = {"team": t, **info, "elo_win": out["win"], "elo_loss": out["loss"]}
    return {"win_prob_a": pa, "win_prob_b": 1 - pa, "players": players}


def ranks_table():
    return [{"rank": i + 1, "rank_name": n, "rank_short": s, "rank_color": c, "min_elo": None if i == 0 else lo}
            for i, (n, s, c, lo) in enumerate(RANKS)]
