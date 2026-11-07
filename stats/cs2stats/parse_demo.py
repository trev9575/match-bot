"""Parse a CS2 .dem file (e.g. MatchZy's recordings) into MatchData using demoparser2."""
from __future__ import annotations

import bisect
import os
import re
from datetime import datetime

from .model import MatchData, Round, Kill, Damage, Blind, T, CT, clean_steamid
from .rating3 import equip_strength

# MatchZy filename: 2026-11-04_20-20-59_3_de_wrecked_team_a_vs_team_b.dem
MATCHZY_NAME = re.compile(r"^(\d{4}-\d\d-\d\d)_(\d\d-\d\d-\d\d)_(\d+)_(.+?)_team_")


def _df(parser, event, **kw):
    try:
        df = parser.parse_event(event, **kw)
    except Exception:
        try:
            df = parser.parse_event(event)
        except Exception:
            return []
    if df is None or len(df) == 0:
        return []
    return df.to_dict("records")


def _side(v):
    if v in (2, "2", "T", "TERRORIST", "t"):
        return T
    if v in (3, "3", "CT", "ct"):
        return CT
    try:
        return {2: T, 3: CT}.get(int(v))
    except (TypeError, ValueError):
        return None


def _not_warmup(e):
    return not bool(e.get("is_warmup_period", False))


def parse_demo(path: str) -> MatchData:
    from demoparser2 import DemoParser   # heavy import, keep local

    parser = DemoParser(path)
    header = parser.parse_header() or {}
    map_name = header.get("map_name", "unknown")

    other = ["is_warmup_period", "total_rounds_played"]
    ends = [e for e in _df(parser, "round_end", other=other) if _not_warmup(e)]
    freezes = sorted(int(e["tick"]) for e in _df(parser, "round_freeze_end", other=other)
                     if _not_warmup(e))
    equip = ["current_equip_value"]
    deaths = [e for e in _df(parser, "player_death", player=equip, other=other) if _not_warmup(e)]
    hurts = [e for e in _df(parser, "player_hurt", other=other) if _not_warmup(e)]
    plants = [e for e in _df(parser, "bomb_planted", other=other) if _not_warmup(e)]
    defuses = [e for e in _df(parser, "bomb_defused", other=other) if _not_warmup(e)]
    blinds = [e for e in _df(parser, "player_blind", other=other) if _not_warmup(e)]
    try:
        purchases = len([e for e in _df(parser, "item_purchase", other=other) if _not_warmup(e)])
        if purchases == 0 and "item_purchase" not in (parser.list_game_events() or []):
            purchases = None          # event not in this demo -> can't tell
    except Exception:
        purchases = None

    # de-dupe round_end and keep only the final live segment (after any restart)
    ends.sort(key=lambda e: int(e["tick"]))
    clean = []
    for e in ends:
        if _side(e.get("winner")) is None:
            continue
        if clean and int(e["tick"]) - int(clean[-1]["tick"]) < 64:
            continue
        clean.append(e)
    if clean and "total_rounds_played" in clean[0]:
        start = 0
        for i in range(1, len(clean)):
            try:
                if int(clean[i]["total_rounds_played"]) < int(clean[i - 1]["total_rounds_played"]):
                    start = i
            except (TypeError, ValueError):
                pass
        clean = clean[start:]

    # round i covers [start_i, start_{i+1}) so post-round kills stay in round i
    starts, prev_end = [], 0
    for e in clean:
        end_t = int(e["tick"])
        cands = [f for f in freezes if prev_end <= f < end_t]
        starts.append(cands[-1] if cands else prev_end)
        prev_end = end_t

    rounds = [Round(number=i + 1, winner=_side(e.get("winner")), start_tick=starts[i],
                    end_tick=int(e["tick"])) for i, e in enumerate(clean)]

    def round_for(tick):
        i = bisect.bisect_right(starts, tick) - 1
        return rounds[i] if 0 <= i < len(rounds) else None

    # rosters: who was on which side when each round went live
    names: dict[str, str] = {}
    if rounds:
        try:
            snap = parser.parse_ticks(["team_num", "current_equip_value"], ticks=[s + 8 for s in starts])
            for r in snap.to_dict("records"):
                sid = clean_steamid(r.get("steamid"))
                side = _side(r.get("team_num"))
                rnd = round_for(int(r["tick"]))
                if sid and side and rnd:
                    rnd.roster[sid] = side
                    if r.get("current_equip_value") is not None:
                        rnd.loadout[sid] = equip_strength(r["current_equip_value"])
                    if r.get("name"):
                        names[sid] = r["name"]
        except Exception:
            pass   # fall back to rosters inferred from events below

    def remember(e, prefix):
        sid = clean_steamid(e.get(f"{prefix}_steamid"))
        if sid and e.get(f"{prefix}_name"):
            names[sid] = e[f"{prefix}_name"]
        return sid

    for e in deaths:
        rnd = round_for(int(e["tick"]))
        v = remember(e, "user")
        if not rnd or not v:
            continue
        a = remember(e, "attacker")
        asst = remember(e, "assister")
        flash = bool(e.get("assistedflash"))
        a_eq, v_eq = e.get("attacker_current_equip_value"), e.get("user_current_equip_value")
        rnd.kills.append(Kill(tick=int(e["tick"]), attacker=a, victim=v,
                              assister=None if flash else asst,
                              flash_assister=asst if flash else None,
                              headshot=bool(e.get("headshot")), weapon=str(e.get("weapon") or ""),
                              attacker_strength=equip_strength(a_eq) if a and a_eq is not None else None,
                              victim_strength=equip_strength(v_eq) if v_eq is not None else None))
    for e in plants:
        rnd = round_for(int(e["tick"]))
        if rnd and rnd.plant_tick is None:
            rnd.plant_tick, rnd.planter = int(e["tick"]), clean_steamid(e.get("user_steamid"))
    for e in defuses:
        rnd = round_for(int(e["tick"]))
        if rnd:
            rnd.defuser = clean_steamid(e.get("user_steamid"))
    for e in hurts:
        rnd = round_for(int(e["tick"]))
        v = clean_steamid(e.get("user_steamid"))
        if rnd and v:
            rnd.damages.append(Damage(tick=int(e["tick"]), attacker=clean_steamid(e.get("attacker_steamid")),
                                      victim=v, dmg=int(e.get("dmg_health") or 0),
                                      weapon=str(e.get("weapon") or "")))
    for e in blinds:
        rnd = round_for(int(e["tick"]))
        v = clean_steamid(e.get("user_steamid"))
        if rnd and v:
            rnd.blinds.append(Blind(tick=int(e["tick"]), attacker=clean_steamid(e.get("attacker_steamid")),
                                    victim=v, duration=float(e.get("blind_duration") or 0)))

    # If tick snapshots failed, infer rosters from team numbers on events.
    if rounds and not any(r.roster for r in rounds):
        try:
            info = parser.parse_player_info().to_dict("records")
        except Exception:
            info = []
        for r in info:
            sid = clean_steamid(r.get("steamid"))
            if sid:
                names.setdefault(sid, r.get("name", sid))
        _infer_rosters(rounds)

    fname = os.path.basename(path)
    mz = MATCHZY_NAME.match(fname)
    played = None
    if mz:
        played = datetime.strptime(f"{mz.group(1)} {mz.group(2)}", "%Y-%m-%d %H-%M-%S").isoformat()
    else:
        played = datetime.fromtimestamp(os.path.getmtime(path)).isoformat(timespec="seconds")

    return MatchData(map_name=map_name, source_name=fname, rounds=rounds, names=names,
                     matchzy_id=mz.group(3) if mz else None, played_at=played,
                     purchases=purchases)


def _infer_rosters(rounds):
    """Last resort: two players are teammates if one never damages/kills the
    other. Sides swap at halftime, which we detect via kills."""
    for rnd in rounds:
        for k in rnd.kills:
            if k.attacker and k.attacker != k.victim:
                rnd.roster.setdefault(k.victim, 0)
                rnd.roster.setdefault(k.attacker, 0)
    # Without team numbers we can't reliably assign sides; leave a clear signal.
    raise RuntimeError("Could not read team rosters from demo (parse_ticks failed).")
