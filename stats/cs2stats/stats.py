"""Per-match stat computation. Pure Python, no I/O — easy to test.

Definitions follow HLTV / Leetify conventions where possible:
  * ADR       – damage to enemies per round, capped at the victim's remaining HP
  * KAST      – % of rounds with a Kill, Assist, Survival or being Traded
  * Opening   – the first kill of a round (entry duel)
  * Trade     – killing the enemy who killed a teammate within TRADE_WINDOW
  * Clutch    – being the last player alive on your side vs >=1 enemy
  * Rating    – community approximation of HLTV Rating 2.0
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, asdict, field

from .model import MatchData, Round, T, CT, TICKRATE
from .rating3 import eco_weight, round_swing, MK_POINTS

DEFAULT_STRENGTH = 0.6        # used when a parser has no loadout info (eco-neutral)

TRADE_WINDOW = 5 * TICKRATE
UTIL_WEAPONS = {"hegrenade", "inferno", "molotov", "incgrenade"}
KNIFE_PREFIXES = ("knife", "bayonet")
FLASH_MIN = 1.0   # seconds; matches the in-game "enemies flashed" counter


@dataclass
class PlayerMatch:
    steamid: str
    name: str = ""
    team: str = ""            # "A" (started T) or "B" (started CT)
    result: str = ""          # W / L / T
    rounds: int = 0
    rounds_won: int = 0
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    hs_kills: int = 0
    damage: float = 0
    util_damage: float = 0
    kast_rounds: int = 0
    entry_attempts: int = 0
    entry_wins: int = 0
    entry_attempts_t: int = 0   # opening duels split by side
    entry_wins_t: int = 0
    entry_attempts_ct: int = 0
    entry_wins_ct: int = 0
    knife_kills: int = 0
    awp_kills: int = 0
    plants: int = 0
    defuses: int = 0
    trade_kills: int = 0
    traded_deaths: int = 0
    clutch_attempts: int = 0
    clutch_wins: int = 0
    k2: int = 0
    k3: int = 0
    k4: int = 0
    k5: int = 0
    aces: int = 0              # killed the entire enemy team (works for 6v6 too)
    enemies_flashed: int = 0
    blind_time: float = 0
    team_flashed: int = 0
    flash_assists: int = 0
    teamkills: int = 0
    # Rating 3.0 inputs
    e_kills: float = 0          # eco-adjusted kills
    e_deaths: float = 0         # eco-adjusted deaths
    e_damage: float = 0         # eco-adjusted damage
    swing: float = 0            # summed round-win-probability swing (fraction of a round)
    mk_points: float = 0
    saved_lost: int = 0         # survived a lost round
    rating: float = 0           # Rating 2.0 approximation (kept for reference)
    clutches: list = field(default_factory=list)   # e.g. ["1v2 W", "1v1 L"]

    def as_row(self) -> dict:
        d = asdict(self)
        d["clutches"] = ",".join(self.clutches)
        return d


def rating2(kills, deaths, assists, kast_rounds, damage, rounds) -> float:
    """Widely used reverse-engineered approximation of HLTV Rating 2.0."""
    if rounds <= 0:
        return 0.0
    kpr, dpr, apr = kills / rounds, deaths / rounds, assists / rounds
    kast = 100 * kast_rounds / rounds
    adr = damage / rounds
    impact = 2.13 * kpr + 0.42 * apr - 0.41
    r = 0.0073 * kast + 0.3591 * kpr - 0.5329 * dpr + 0.2372 * impact + 0.0032 * adr + 0.1587
    return round(r, 2)


def is_knife_round(rnd: Round) -> bool:
    """Every player kill in the round was explicitly made with a knife."""
    kills = [k for k in rnd.kills if k.attacker and k.attacker != k.victim]
    return bool(kills) and all(k.weapon.startswith(KNIFE_PREFIXES) for k in kills)


def _active_players(rounds) -> set:
    act = set()
    for r in rounds:
        for k in r.kills:
            act.update(x for x in (k.attacker, k.victim, k.assister, k.flash_assister) if x)
        act.update(d.attacker for d in r.damages if d.attacker)
        act.update(b.attacker for b in r.blinds if b.attacker and b.duration > FLASH_MIN)
    return act


def compute(match: MatchData) -> tuple[dict[str, PlayerMatch], dict]:
    rounds = [r for r in match.rounds if not is_knife_round(r)]
    # AFK players / spectators parked on a team never do anything; take them off
    # the rosters up front so they don't count as "alive" in win probabilities.
    active = _active_players(rounds) | set(match.damage_override)
    if active:
        for r in rounds:
            r.roster = {s: sd for s, sd in r.roster.items() if s in active}
    P: dict[str, PlayerMatch] = {}

    def p(sid: str) -> PlayerMatch:
        if sid not in P:
            P[sid] = PlayerMatch(steamid=sid, name=match.names.get(sid, sid))
        return P[sid]

    # Team identity: players who started on T are team A, CT are team B.
    # Late joiners take the team of the side they first appear on, adjusted
    # for halftime by comparing with players whose team is already known.
    team_of: dict[str, str] = {}
    for rnd in rounds:
        known = {side: [team_of[s] for s, sd in rnd.roster.items() if sd == side and s in team_of]
                 for side in (T, CT)}
        for sid, side in rnd.roster.items():
            if sid in team_of:
                continue
            if known[side]:
                team_of[sid] = max(set(known[side]), key=known[side].count)
            elif not team_of:
                team_of[sid] = "A" if side == T else "B"
            else:
                other = CT if side == T else T
                team_of[sid] = ("B" if known[other][0] == "A" else "A") if known[other] else (
                    "A" if side == T else "B")

    team_score = {"A": 0, "B": 0}
    side_team_by_round = []
    pairs = defaultdict(lambda: [0, 0, 0])   # (killer, victim) -> [kills, headshots, opening kills]
    round_log = []              # per round: "A"/"B" winner (lowercase = won while on T side), "-" = unknown

    for rnd in rounds:
        roster = rnd.roster
        side_team = {}
        for sid, side in roster.items():
            side_team.setdefault(side, team_of.get(sid))
        side_team_by_round.append(side_team)
        if rnd.winner in side_team and side_team[rnd.winner]:
            team_score[side_team[rnd.winner]] += 1
            w = side_team[rnd.winner]
            round_log.append(w.lower() if rnd.winner == T else w)
        else:
            round_log.append("-")

        for sid in roster:
            pm = p(sid)
            pm.rounds += 1
            if rnd.winner is not None and roster[sid] == rnd.winner:
                pm.rounds_won += 1

        def side(sid):
            return roster.get(sid)

        def enemies(a, v):
            return a is not None and v is not None and side(a) is not None and \
                side(v) is not None and side(a) != side(v)

        strengths = {sid: rnd.loadout.get(sid, DEFAULT_STRENGTH) if rnd.loadout else DEFAULT_STRENGTH
                     for sid in roster}

        # ---- damage (capped at remaining HP) ----
        hp = defaultdict(lambda: 100)
        for d in sorted(rnd.damages, key=lambda x: x.tick):
            actual = min(max(d.dmg, 0), hp[d.victim])
            hp[d.victim] -= actual
            if d.attacker and d.attacker != d.victim and enemies(d.attacker, d.victim):
                p(d.attacker).damage += actual
                p(d.attacker).e_damage += actual * eco_weight(strengths.get(d.attacker), strengths.get(d.victim))
                if d.weapon in UTIL_WEAPONS:
                    p(d.attacker).util_damage += actual

        # ---- flashes ----
        for b in rnd.blinds:
            if not b.attacker or b.duration <= FLASH_MIN or b.attacker == b.victim:
                continue
            if enemies(b.attacker, b.victim):
                p(b.attacker).enemies_flashed += 1
                p(b.attacker).blind_time += b.duration
            elif side(b.attacker) == side(b.victim):
                p(b.attacker).team_flashed += 1

        if rnd.planter in roster:
            p(rnd.planter).plants += 1
        if rnd.defuser in roster:
            p(rnd.defuser).defuses += 1

        # ---- kills, KAST, entries, trades, clutches ----
        alive = {T: {s for s, sd in roster.items() if sd == T},
                 CT: {s for s, sd in roster.items() if sd == CT}}
        got_k, got_a, died, traded = set(), set(), set(), set()
        round_kills = defaultdict(int)
        recent_deaths = []          # (tick, victim, killer)
        clutch = {}                 # sid -> vs count
        first_kill_done = False

        for k in sorted(rnd.kills, key=lambda x: x.tick):
            v = k.victim
            if v not in roster:
                continue
            p(v).deaths += 1
            a_s = k.attacker_strength if k.attacker_strength is not None else strengths.get(k.attacker)
            v_s = k.victim_strength if k.victim_strength is not None else strengths.get(v)
            ew = eco_weight(a_s, v_s) if (k.attacker and k.attacker != v and enemies(k.attacker, v)) else 1.0
            p(v).e_deaths += ew
            died.add(v)
            alive[roster[v]].discard(v)
            a = k.attacker
            if a and a != v and enemies(a, v):
                pa = p(a)
                pa.kills += 1
                pa.e_kills += ew
                pa.hs_kills += int(k.headshot)
                round_kills[a] += 1
                got_k.add(a)
                if k.weapon.startswith(KNIFE_PREFIXES):
                    pa.knife_kills += 1
                elif k.weapon == "awp":
                    pa.awp_kills += 1
                pair = pairs[(a, v)]
                pair[0] += 1
                pair[1] += int(k.headshot)
                if not first_kill_done:
                    pa.entry_attempts += 1
                    pa.entry_wins += 1
                    p(v).entry_attempts += 1
                    sa, sv = ("t" if side(a) == T else "ct"), ("t" if side(v) == T else "ct")
                    setattr(pa, f"entry_attempts_{sa}", getattr(pa, f"entry_attempts_{sa}") + 1)
                    setattr(pa, f"entry_wins_{sa}", getattr(pa, f"entry_wins_{sa}") + 1)
                    setattr(p(v), f"entry_attempts_{sv}", getattr(p(v), f"entry_attempts_{sv}") + 1)
                    pair[2] += 1
                    first_kill_done = True
                # trade: did the victim kill one of my teammates recently?
                for t, dv, killer in recent_deaths:
                    if killer == v and side(dv) == side(a) and k.tick - t <= TRADE_WINDOW:
                        if dv not in traded:
                            traded.add(dv)
                            p(dv).traded_deaths += 1
                        pa.trade_kills += 1
                        break
            elif a and a != v and side(a) == side(v):
                p(a).teamkills += 1
            recent_deaths.append((k.tick, v, a))

            # Scoreboard rule: flash assists count as assists too (also tracked
            # separately); one credit per helper per kill.
            helpers = {h for h in (k.assister, k.flash_assister) if h and h != v and enemies(h, v)}
            for h in helpers:
                p(h).assists += 1
                got_a.add(h)
            if k.flash_assister in helpers:
                p(k.flash_assister).flash_assists += 1

            # clutch start: a side drops to exactly one player
            for sd in (T, CT):
                other = CT if sd == T else T
                if len(alive[sd]) == 1 and alive[other]:
                    (last,) = tuple(alive[sd])
                    if last not in clutch:
                        clutch[last] = len(alive[other])

        for sid, vs in clutch.items():
            won = rnd.winner is not None and roster.get(sid) == rnd.winner
            pc = p(sid)
            pc.clutch_attempts += 1
            pc.clutch_wins += int(won)
            pc.clutches.append(f"1v{vs} {'W' if won else 'L'}")

        for sid in roster:
            if sid in got_k or sid in got_a or sid not in died or sid in traded:
                p(sid).kast_rounds += 1
            if sid not in died and rnd.winner is not None and roster[sid] != rnd.winner:
                p(sid).saved_lost += 1
        for sid, sw in round_swing(rnd, roster, strengths, enemies).items():
            if sid in roster:
                p(sid).swing += sw
        for sid, n in round_kills.items():
            enemy_count = sum(1 for sd in roster.values() if sd != roster.get(sid))
            if n >= enemy_count >= 4:
                p(sid).aces += 1
            if n >= 2:
                p(sid).mk_points += MK_POINTS[min(n, 5)]
                setattr(p(sid), f"k{min(n, 5)}", getattr(p(sid), f"k{min(n, 5)}") + 1)

    # externally supplied damage totals (log mode)
    has_hits = any(r.damages for r in rounds)
    for sid, o in match.damage_override.items():
        if sid in P:
            for key, val in o.items():
                setattr(P[sid], key, val)
            if not has_hits:
                P[sid].e_damage = P[sid].damage     # log mode: no per-hit data to eco-adjust

    a, b = team_score["A"], team_score["B"]
    for pm in P.values():
        pm.team = team_of.get(pm.steamid, "")
        mine, theirs = (a, b) if pm.team == "A" else (b, a)
        pm.result = "W" if mine > theirs else "L" if mine < theirs else "T"
        pm.damage = round(pm.damage)
        pm.util_damage = round(pm.util_damage)
        pm.blind_time = round(pm.blind_time, 2)
        pm.e_kills, pm.e_deaths = round(pm.e_kills, 3), round(pm.e_deaths, 3)
        pm.e_damage, pm.swing = round(pm.e_damage, 1), round(pm.swing, 4)
        pm.rating = rating2(pm.kills, pm.deaths, pm.assists, pm.kast_rounds, pm.damage, pm.rounds)

    # drop spectators / AFK ghosts: on a team's roster but never did anything
    P = {s: pm for s, pm in P.items() if pm.rounds > 0 and
         (pm.kills or pm.deaths or pm.assists or pm.damage or pm.enemies_flashed or pm.teamkills)}
    summary = {"rounds": len(rounds), "score_a": a, "score_b": b, "map": match.map_name,
               "game_mode": match.game_mode, "purchases": match.purchases,
               "round_log": "".join(round_log),
               "pairs": [(a_, v_, n, hs, op) for (a_, v_), (n, hs, op) in pairs.items()
                         if a_ in P and v_ in P]}
    return P, summary
