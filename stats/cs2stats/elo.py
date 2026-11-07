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
RANK_EXPIRE_DAYS = env_int("RANK_EXPIRE_DAYS", 30)    # CS:GO hid your rank after ~a month idle; 0 = never

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
    ("Distinguished Master Guardian", "DMG", "#5cc6f0", 1775),
    ("Legendary Eagle", "LE", "#8b6fe0", 1850),
    ("Legendary Eagle Master", "LEM", "#9b7ff0", 1925),
    ("Supreme Master First Class", "SMFC", "#e0576b", 2000),
    ("The Global Elite", "GE", "#f2c94c", 2100),
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


def compute(rows: list[dict], now: str | None = None):
    """rows: one per player per match with match_id, played_at, steamid, team, result.

    Returns (players, per_match):
      players[steamid]   = {elo, rd, matches, placed, rank..., last_played, inactive}
      per_match[(match_id, steamid)] = {elo_before, elo_after, elo_delta}
    """
    by_match: dict[int, dict] = {}
    for r in rows:
        m = by_match.setdefault(r["match_id"], {"played_at": r["played_at"] or "", "players": []})
        m["players"].append(r)
    state: dict[str, dict] = {}
    per_match = {}
    for mid, m in sorted(by_match.items(), key=lambda kv: (kv[1]["played_at"], kv[0])):
        teams = {"A": [], "B": []}
        for p in m["players"]:
            if p["team"] in teams:
                teams[p["team"]].append(p)
        if not teams["A"] or not teams["B"]:
            continue
        # bring everyone's uncertainty up to date before the match
        for p in m["players"]:
            s = state.setdefault(p["steamid"], {"elo": START, "rd": START_RD, "vol": START_VOL,
                                                "matches": 0, "last": None})
            if s["last"]:
                s["rd"] = _idle_rd(s["rd"], s["vol"], _days(s["last"], m["played_at"]))
        avg = {t: (sum(state[p["steamid"]]["elo"] for p in ps) / len(ps),
                   math.sqrt(sum(state[p["steamid"]]["rd"] ** 2 for p in ps) / len(ps)))
               for t, ps in teams.items()}
        updates = {}
        for t, ps in teams.items():
            opp_r, opp_rd = avg["B" if t == "A" else "A"]
            for p in ps:
                s = state[p["steamid"]]
                score = {"W": 1.0, "L": 0.0}.get(p["result"], 0.5)
                updates[p["steamid"]] = glicko2_update(s["elo"], s["rd"], s["vol"], [(opp_r, opp_rd, score)])
        for sid, (r, rd, vol) in updates.items():
            s = state[sid]
            per_match[(mid, sid)] = {"elo_before": round(s["elo"]), "elo_after": round(r),
                                     "elo_delta": round(r) - round(s["elo"])}
            s.update(elo=r, rd=rd, vol=vol, matches=s["matches"] + 1, last=m["played_at"])

    now = now or datetime.now().isoformat(timespec="seconds")
    players = {}
    for sid, s in state.items():
        placed = s["matches"] >= PLACEMENT_MATCHES
        inactive = bool(RANK_EXPIRE_DAYS) and _days(s["last"], now) > RANK_EXPIRE_DAYS
        info = {"elo": round(s["elo"]), "elo_rd": round(s["rd"]), "elo_matches": s["matches"],
                "placed": placed, "inactive": inactive, "placement_needed": PLACEMENT_MATCHES}
        info.update(rank_for(s["elo"]) if placed and not inactive else
                    {"rank": 0, "rank_short": "—", "rank_color": "#6d6d6d",
                     "rank_name": "Rank expired" if placed else f"Unranked ({s['matches']}/{PLACEMENT_MATCHES})"})
        players[sid] = info
    return players, per_match


def ranks_table():
    return [{"rank": i + 1, "rank_name": n, "rank_short": s, "rank_color": c, "min_elo": None if i == 0 else lo}
            for i, (n, s, c, lo) in enumerate(RANKS)]
