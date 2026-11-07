"""Approximation of HLTV Rating 3.0 (as of the Oct 2025 / Oct 2026 updates).

What HLTV has published, and what this module follows:
  * Six sub-ratings: Kills, Damage, Survival, KAST, Multi-Kill, Round Swing.
  * Kills, Damage and Survival are eco-adjusted by how likely a duel was to be
    won given each player's equipment (eco frags count less).
  * Round Swing = change in the team's round-win probability caused by each
    kill (and plant/defuse), credited to the killer, damage dealers and flash
    assisters. Round-end credit is shared: survivors 1 share, players who made
    advantage-building kills 2, clutchers +1, defusers +1.
  * Kills after the round is decided (exit frags, or <10s before an
    unstoppable bomb) give no swing; a kill can't produce negative swing.
  * Weights after the Oct 2025 update: Kills 25%, Round Swing 33%; the rest
    keeps a 60/40 output-vs-survival balance.
  * Rating 1.00 = average player of the pool it's normalized against.

What HLTV keeps private and is modeled here instead: the win-probability
tables (they're fit on ~29k pro maps), the duel-win model, exact credit
shares, and normalization constants. HLTV normalizes against pro averages;
we normalize against *your group*, so 1.00 = average player in your games.
"""
from __future__ import annotations

import math
from collections import defaultdict

from .model import Round, T, CT, TICKRATE

WEIGHTS = {"kills": 0.25, "swing": 0.33, "damage": 0.11, "multikill": 0.05,
           "survival": 0.14, "kast": 0.12}
RATING_SPREAD = 0.30          # per-map SD of rating, roughly HLTV-like
BOMB_TIMER = 40 * TICKRATE

# --- equipment -------------------------------------------------------------
# HLTV's six equipment groups -> strength 0..1
SNIPERS = {"awp", "g3sg1", "scar20"}
T1_RIFLES = {"ak47", "m4a1", "m4a1_silencer", "m4a1_silencer_off", "aug", "sg556"}
T2_RIFLES = {"famas", "galilar", "ssg08"}
SMGS = {"mac10", "mp9", "mp7", "mp5sd", "ump45", "p90", "bizon", "nova", "xm1014",
        "sawedoff", "mag7", "m249", "negev"}
UPG_PISTOLS = {"deagle", "p250", "fiveseven", "tec9", "cz75a", "revolver", "elite"}
STARTERS = {"glock", "usp_silencer", "usp_silencer_off", "hkp2000"}
STRENGTH = {**{w: 1.0 for w in SNIPERS | T1_RIFLES}, **{w: 0.75 for w in T2_RIFLES},
            **{w: 0.45 for w in SMGS}, **{w: 0.2 for w in UPG_PISTOLS},
            **{w: 0.0 for w in STARTERS}}
PRIMARY = SNIPERS | T1_RIFLES | T2_RIFLES | SMGS


def weapon_strength(weapon: str):
    w = (weapon or "").lower().replace("weapon_", "")
    return STRENGTH.get(w)


def equip_strength(equip_value) -> float:
    """Demo mode: map a player's current equipment value ($) to strength.
    ~$1,200 (pistol+kevlar) -> ~0.07, SMG+armor ~0.45, rifle+armor >= 1."""
    try:
        return max(0.0, min(1.0, (float(equip_value) - 1000) / 2700))
    except (TypeError, ValueError):
        return 0.0


def duel_p(s_att: float, s_vic: float) -> float:
    """Expected chance the attacker wins this duel given equipment."""
    return 1 / (1 + math.exp(-2.2 * (s_att - s_vic)))


def eco_weight(s_att, s_vic) -> float:
    """1.0 for an even duel; ~0.6 for rifle-vs-glock, ~1.4 for the reverse.
    Applied to the kill (credit) and to the death (penalty)."""
    if s_att is None or s_vic is None:
        return 1.0
    return 1.0 + (0.5 - duel_p(s_att, s_vic))


# --- round win probability -------------------------------------------------
def p_ct_wins(n_ct, n_t, s_ct, s_t, planted) -> float:
    """Modeled P(CT wins round). Calibrated to well-known CS manpower odds:
    5v4 ≈ 68%, 1v2 ≈ 21%, 1v3 ≈ 9%, 1v5 ≈ 2%; planted bomb swings ~1 logit to T."""
    if n_ct <= 0:
        return 0.0
    if n_t <= 0:
        return 0.88 if planted else 1.0
    logit = 0.1 + 1.6 * (n_ct - n_t) / math.sqrt((n_ct + n_t) / 2)
    logit += 1.5 * (s_ct - s_t)
    if planted:
        logit -= 1.0
    return 1 / (1 + math.exp(-logit))


def round_swing(rnd: Round, roster: dict, strengths: dict, enemies) -> dict[str, float]:
    """Return {steamid: swing} for one round. Positive = helped your team."""
    swing = defaultdict(float)
    alive = {T: {s for s, sd in roster.items() if sd == T},
             CT: {s for s, sd in roster.items() if sd == CT}}
    s_now = {s: strengths.get(s, 0.0) for s in roster}
    planted = False
    killers = defaultdict(int)
    clutchers = set()

    def p_side(side):
        def mean(ss):
            return sum(s_now[x] for x in ss) / len(ss) if ss else 0.0
        pct = p_ct_wins(len(alive[CT]), len(alive[T]), mean(alive[CT]), mean(alive[T]), planted)
        return pct if side == CT else 1 - pct

    end = rnd.end_tick if rnd.end_tick is not None else float("inf")
    dead_line = rnd.plant_tick + BOMB_TIMER - 10 * TICKRATE if rnd.plant_tick is not None else None

    # damage dealt to each victim so far, for credit sharing
    dmg_by = defaultdict(lambda: defaultdict(float))
    hits = sorted(rnd.damages, key=lambda d: d.tick)
    hi = 0

    events = [("k", k.tick, k) for k in rnd.kills]
    if rnd.plant_tick is not None:
        events.append(("p", rnd.plant_tick, None))
    events.sort(key=lambda e: (e[1], e[0] != "p"))

    for kind, tick, k in events:
        while hi < len(hits) and hits[hi].tick <= tick:
            h = hits[hi]
            if h.attacker and enemies(h.attacker, h.victim):
                dmg_by[h.victim][h.attacker] += max(h.dmg, 0)
            hi += 1
        if tick > end:
            break                                  # exit frags: no swing
        if kind == "p":
            before = p_side(T)
            planted = True
            gain = p_side(T) - before
            if rnd.planter in roster and gain > 0:
                swing[rnd.planter] += gain
                ct_alive = alive[CT] or {s for s, sd in roster.items() if sd == CT}
                for s in ct_alive:
                    swing[s] -= gain / len(ct_alive)
            continue

        v = k.victim
        if v not in roster or v not in alive[roster[v]]:
            continue
        side_v = roster[v]
        side_k = CT if side_v == T else T
        if k.attacker_strength is not None and k.attacker in s_now:
            s_now[k.attacker] = k.attacker_strength
        before = p_side(side_k)
        alive[side_v].discard(v)
        after = p_side(side_k)
        gain = max(0.0, after - before)
        if dead_line is not None and tick >= dead_line and side_v == CT:
            gain = 0.0                             # bomb can't be stopped anymore
        if k.attacker and enemies(k.attacker, v):
            killers[k.attacker] += 1
        for sd in (T, CT):                         # clutch: side down to one player
            if len(alive[sd]) == 1 and alive[CT if sd == T else T]:
                clutchers |= alive[sd]
        if gain <= 0:
            continue
        # credit split: killer base + damage share + flash assist
        shares = {}
        team_dmg = {a: d for a, d in dmg_by[v].items() if roster.get(a) == side_k}
        total = sum(team_dmg.values())
        killer = k.attacker if (k.attacker and enemies(k.attacker, v)) else None
        flash = k.flash_assister if (k.flash_assister and enemies(k.flash_assister, v)) else None
        base = 0.35 if killer else 0.0
        if total > 0:
            for a, d in team_dmg.items():
                shares[a] = shares.get(a, 0) + 0.50 * d / total
        elif killer:
            shares[killer] = shares.get(killer, 0) + 0.50
            if k.assister and enemies(k.assister, v):
                shares[killer] -= 0.20
                shares[k.assister] = shares.get(k.assister, 0) + 0.20
        if killer:
            shares[killer] = shares.get(killer, 0) + base
        if flash:
            shares[flash] = shares.get(flash, 0) + 0.15
        elif killer:
            shares[killer] += 0.15
        if not shares:                             # suicide / fall / world kill
            shares = {s: 1.0 for s in (alive[side_k] or
                                       {x for x, sd in roster.items() if sd == side_k})}
        norm = sum(shares.values()) or 1
        for a, sh in shares.items():
            swing[a] += gain * sh / norm
        swing[v] -= gain

    # round-end residual
    if rnd.winner in (T, CT):
        w, l = rnd.winner, (CT if rnd.winner == T else T)
        residual = 1.0 - p_side(w)
        if residual > 1e-6:
            sh = defaultdict(float)
            for s, sd in roster.items():
                if sd != w:
                    continue
                if s in alive[w]:
                    sh[s] += 1
                if killers.get(s):
                    sh[s] += 2
                if s in clutchers:
                    sh[s] += 1
                if s == rnd.defuser:
                    sh[s] += 1
            if not sum(sh.values()):               # e.g. bomb exploded after all Ts died
                for s, sd in roster.items():
                    if sd == w:
                        sh[s] = 1
            tot = sum(sh.values())
            if tot:
                for s, x in sh.items():
                    swing[s] += residual * x / tot
            losers = alive[l] or {s for s, sd in roster.items() if sd == l}
            for s in losers:
                swing[s] -= residual / len(losers)
    return swing


MK_POINTS = {2: 1.0, 3: 2.2, 4: 3.5, 5: 5.0}


def has_inputs(row: dict) -> bool:
    """False for rows imported before Rating 3.0 existed (no eco/swing data)."""
    if "r3_complete" in row:
        return bool(row["r3_complete"])
    return bool(row.get("e_kills") or row.get("e_deaths") or row.get("swing"))


def subrating_inputs(row: dict) -> dict:
    """Per-round rates for the six sub-ratings from summed counters."""
    r = max(row.get("rounds") or 0, 1)
    return {
        "kills": (row.get("e_kills") or 0) / r,
        "damage": (row.get("e_damage") or 0) / r,
        "survival": (r - (row.get("e_deaths") or 0) - 0.5 * (row.get("saved_lost") or 0)) / r,
        "kast": (row.get("kast_rounds") or 0) / r,
        "multikill": (row.get("mk_points") or 0) / r,
        "swing": (row.get("swing") or 0) / r,
    }


def baseline(samples: list[dict]) -> dict:
    """Round-weighted mean/SD of each sub-rating over per-player-map samples,
    plus SD of the composite so ratings land at mean 1.00 / SD RATING_SPREAD."""
    xs = [(max(s.get("rounds") or 0, 1), subrating_inputs(s)) for s in samples
          if s.get("rounds") and has_inputs(s)]
    if len(xs) < 4:
        return {}
    tw = sum(w for w, _ in xs)
    out = {}
    for k in WEIGHTS:
        mu = sum(w * x[k] for w, x in xs) / tw
        var = sum(w * (x[k] - mu) ** 2 for w, x in xs) / tw
        out[k] = (mu, max(math.sqrt(var), 1e-6))
    comps = [sum(WEIGHTS[k] * (x[k] - out[k][0]) / out[k][1] for k in WEIGHTS) for _, x in xs]
    cm = sum(w * c for (w, _), c in zip(xs, comps)) / tw
    csd = math.sqrt(sum(w * (c - cm) ** 2 for (w, _), c in zip(xs, comps)) / tw) or 1.0
    out["_composite"] = (cm, csd)
    return out


def rating3(row: dict, base: dict):
    """Returns (rating, {subrating: value}) — sub-ratings also centered at 1.00."""
    if not base or not has_inputs(row):
        return None, {}
    x = subrating_inputs(row)
    z = {k: (x[k] - base[k][0]) / base[k][1] for k in WEIGHTS}
    cm, csd = base["_composite"]
    comp = sum(WEIGHTS[k] * z[k] for k in WEIGHTS)
    rating = 1.0 + RATING_SPREAD * (comp - cm) / csd
    subs = {k: round(1.0 + RATING_SPREAD * z[k], 2) for k in WEIGHTS}
    return round(rating, 2), subs
