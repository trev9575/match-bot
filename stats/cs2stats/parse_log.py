"""Parse CS2 server logs (or an AMP console log) into MatchData.

Used as a fallback when a demo isn't available and to cross-check the demo
parser. Log mode limitations: 1-second timestamp resolution (trade windows are
approximate) and, unless `mp_logdetail 3` is set, no per-hit damage lines; in
that case damage / utility damage / enemies flashed come from the server's own
`round_stats` totals.
"""
from __future__ import annotations

import re
from datetime import datetime

from .model import MatchData, Round, Kill, Blind, Damage, T, CT, TICKRATE, steam3_to_64
from .rating3 import weapon_strength, PRIMARY

LINE = re.compile(r"L (\d\d/\d\d/\d{4} - \d\d:\d\d:\d\d): (.*)")
PLAYER = r'"(?P<{p}name>.*?)<\d+><(?P<{p}sid>\[U:1:\d+\]|BOT)><(?P<{p}team>[A-Za-z]*)>"'
A, V = PLAYER.format(p="a"), PLAYER.format(p="v")
POS = r"(?: \[[-\d ]+\])?"

RE_KILL = re.compile(A + POS + r" killed " + V + POS + r' with "(?P<weapon>[^"]+)"(?P<flags>.*)')
RE_ASSIST = re.compile(A + r" (?P<kind>assisted|flash-assisted) killing " + V)
RE_BLIND = re.compile(V + r" blinded for (?P<dur>[\d.]+) by " + A + r" from flashbang")
RE_ATTACK = re.compile(A + POS + r" attacked " + V + POS +
                       r' with "(?P<weapon>[^"]+)" \(damage "(?P<dmg>\d+)"\)')
RE_SUICIDE = re.compile(V + POS + r' committed suicide with "(?P<weapon>[^"]+)"')
RE_TEAMWIN = re.compile(r'Team "(?P<team>CT|TERRORIST)" triggered "SFUI_Notice_(?P<how>\w+)"')
RE_BUY = re.compile(PLAYER.format(p="") + r' purchased "(?P<item>[^"]+)"')
RE_PLANT = re.compile(PLAYER.format(p="") + r' triggered "Planted_The_Bomb"')
RE_DEFUSE_START = re.compile(PLAYER.format(p="") + r' triggered "Begin_Bomb_Defuse')
RE_SWITCH = re.compile(r'"(?P<name>.*?)<\d+><(?P<sid>\[U:1:\d+\])>" switched from team <\w*> to <(?P<team>\w+)>')
RE_DISCO = re.compile(PLAYER.format(p="") + r" disconnected")
RE_TAGGED = re.compile(PLAYER.format(p=""))
RE_GAMEOVER = re.compile(r"Game Over: (?P<mode>\w+)\s+(?P<map>\S+) score (?P<s1>\d+):(?P<s2>\d+)")
RE_MATCHID = re.compile(r"\[HandleMatchEnd\].*matchid: (\d+)")
RE_DEMO = re.compile(r"\[StartDemoRecoding\].*path: (\S+\.dem)")

SIDE = {"TERRORIST": T, "CT": CT}


def _sid(raw: str):
    if not raw or raw == "BOT":
        return None
    return steam3_to_64(raw[5:-1])


def _round_stats_blocks(lines):
    """Yield (line_index, {steamid64: {field: value}}, round_number) for each
    round_stats JSON block. Repairs player rows split by interleaved output."""
    i = 0
    while i < len(lines):
        if "JSON_BEGIN{" not in lines[i]:
            i += 1
            continue
        start = i
        body, fields, rnum = [], None, None
        i += 1
        while i < len(lines) and "JSON_END" not in lines[i]:
            body.append(lines[i])
            i += 1
        if not any('"round_stats"' in b for b in body):
            continue
        players = {}
        j = 0
        while j < len(body):
            raw = body[j]
            m = LINE.search(raw)
            txt = m.group(2) if m else raw
            if '"fields"' in txt:
                fields = [f.strip() for f in txt.split(":", 1)[1].strip().strip('"').split(",")]
            elif '"round_number"' in txt:
                rnum = int(re.findall(r"\d+", txt.split(":", 1)[1])[0])
            elif re.match(r'\s*"player_\d+"', txt):
                val = txt.split(":", 1)[1].strip().lstrip('"')
                if not val.endswith('"'):           # spliced — find continuation
                    val = val.split("[MatchZy]")[0]
                    for k in range(j + 1, len(body)):
                        cont = body[k]
                        if not cont.startswith(("L ", "[")) and cont.rstrip().endswith('"'):
                            val += cont
                            break
                vals = [x.strip() for x in val.rstrip('"').split(",")]
                if fields and "accountid" in fields and len(vals) >= len(fields):
                    row = dict(zip(fields, vals))
                    try:
                        players[steam3_to_64(row["accountid"])] = {
                            k: float(v) for k, v in row.items() if k != "accountid"}
                    except ValueError:
                        pass
            j += 1
        yield start, players, rnum


SPLICE = re.compile(r"\[(MatchZy|CSSharp|DemoRecorder)\]|\[\d\d:\d\d:\d\d\.\d+\] ")


def repair_lines(lines: list[str]) -> list[str]:
    """AMP's console log mixes plugin stdout into the game log, which can cut a
    game-log line in two: `L ...killed "x<1><[U:1:[MatchZy] ...` and, a few
    lines later, the remainder `2345]><CT>" ...`. Stitch those back together.
    Plain csgo/logs/*.log files don't have this problem; this is a no-op there."""
    out, consumed = [], set()
    for i, line in enumerate(lines):
        if i in consumed:
            continue
        m = SPLICE.search(line)
        if line.startswith("L ") and m and m.start() > 0:
            head, tail = line[:m.start()], line[m.start():]
            for j in range(i + 1, min(i + 40, len(lines))):
                cand = lines[j]
                if j in consumed or cand.startswith("L ") or re.match(r"\[[A-Za-z]", cand):
                    continue
                if cand and not cand.startswith(("\t", "Recording", "playdemo", "-- ", "Summary")):
                    consumed.add(j)
                    head += cand
                    break
            out.append(head)
            out.append(tail)
        else:
            out.append(line)
    return out


def parse_log(path: str) -> list[MatchData]:
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = repair_lines(f.read().splitlines())

    stats_blocks = list(_round_stats_blocks(lines))
    matches: list[MatchData] = []

    team_now: dict[str, int] = {}
    names: dict[str, str] = {}
    rounds: list[Round] = []
    cur: Round | None = None
    t0 = None
    seg_start = 0
    last_demo = None
    buys = 0
    # loadout tracker: best weapon strength each player is carrying
    primary: dict[str, float] = {}
    secondary: dict[str, float] = {}
    last_side: dict[str, int] = {}
    last_defuser = None

    def strength(sid):
        return max(primary.get(sid, 0.0), secondary.get(sid, 0.0))

    def give(sid, weapon):
        s = weapon_strength(weapon)
        if s is None or not sid:
            return
        w = weapon.lower().replace("weapon_", "")
        if w in PRIMARY:
            primary[sid] = s
        else:
            secondary[sid] = max(secondary.get(sid, 0.0), s)

    def strip(sid):
        primary.pop(sid, None)
        secondary.pop(sid, None)

    def tick_of(ts: str) -> int:
        nonlocal t0
        t = datetime.strptime(ts, "%m/%d/%Y - %H:%M:%S")
        if t0 is None:
            t0 = t
        return int((t - t0).total_seconds() * TICKRATE)

    def note(m, p=""):
        sid = _sid(m.group(p + "sid"))
        if sid:
            names[sid] = m.group(p + "name")
            side = SIDE.get(m.group(p + "team"))
            if side:
                if last_side.get(sid, side) != side:   # halftime / OT swap: back to pistols
                    strip(sid)
                last_side[sid] = side
                team_now[sid] = side
                if cur is not None and sid not in cur.roster:
                    cur.roster[sid] = side
        return sid

    for idx, raw in enumerate(lines):
        if "[StartDemoRecoding]" in raw:
            m = RE_DEMO.search(raw)
            last_demo = m.group(1).rsplit("/", 1)[-1] if m else None
        m = LINE.search(raw)
        if not m:
            continue
        ts, ev = m.groups()

        if 'World triggered "Match_Start"' in ev:
            rounds, cur, seg_start, buys = [], None, idx, 0
            primary.clear(); secondary.clear(); last_side.clear()
            continue
        if ' purchased "' in ev:
            buys += 1
            if (bm_ := RE_BUY.search(ev)):
                give(note(bm_), bm_.group("item"))
        if 'World triggered "Round_Start"' in ev:
            cur = Round(number=len(rounds) + 1, winner=None, start_tick=tick_of(ts),
                        roster=dict(team_now))
            cur.loadout = {sid: strength(sid) for sid in cur.roster}
            last_defuser = None
            rounds.append(cur)
            continue
        if (gm := RE_GAMEOVER.search(ev)):
            mid = None
            for nxt in lines[idx: idx + 400]:
                if (mm := RE_MATCHID.search(nxt)):
                    mid = mm.group(1)
                    break
            md = MatchData(map_name=gm.group("map"), source_name=last_demo or f"log:{ts}",
                           rounds=[r for r in rounds if r.winner is not None],
                           names=dict(names), matchzy_id=mid,
                           played_at=datetime.strptime(ts, "%m/%d/%Y - %H:%M:%S").isoformat(),
                           game_mode=gm.group("mode"), purchases=buys)
            blocks = [b for b in stats_blocks if seg_start < b[0] < idx + 50 and b[1]]
            if blocks:
                # highest round number = the match's own final snapshot (the server
                # may print a fresh round-1 block right after Game Over)
                best = max(blocks, key=lambda b: (b[2] or 0, b[0]))
                md.damage_override = {"_round_stats": best}  # resolved below
            matches.append(md)
            rounds, cur = [], None
            continue
        if (sm := RE_SWITCH.search(ev)):
            sid = _sid(sm.group("sid"))
            side = SIDE.get(sm.group("team"))
            if side:
                team_now[sid] = side
            else:
                team_now.pop(sid, None)
            continue
        if (dm := RE_DISCO.search(ev)):
            team_now.pop(_sid(dm.group("sid")) or "", None)
            continue
        if cur is None:
            continue

        if (tw := RE_TEAMWIN.search(ev)):
            cur.winner = SIDE[tw.group("team")]
            cur.end_tick = tick_of(ts)
            if tw.group("how") == "Bomb_Defused":
                cur.defuser = last_defuser
        elif (pl := RE_PLANT.search(ev)):
            cur.plant_tick, cur.planter = tick_of(ts), note(pl)
        elif (ds := RE_DEFUSE_START.search(ev)):
            last_defuser = note(ds)
        elif (km := RE_KILL.search(ev)):
            a, v = note(km, "a"), note(km, "v")
            if v:
                ws = weapon_strength(km.group("weapon"))
                if a and ws is not None:            # what they actually used (incl. pickups)
                    give(a, km.group("weapon"))
                cur.kills.append(Kill(tick=tick_of(ts), attacker=a, victim=v,
                                      headshot="headshot" in km.group("flags"),
                                      weapon=km.group("weapon"),
                                      attacker_strength=strength(a) if a else None,
                                      victim_strength=strength(v)))
                strip(v)                            # dead players drop their guns
        elif (am := RE_ASSIST.search(ev)):
            a, v = note(am, "a"), note(am, "v")
            for k in reversed(cur.kills):
                if k.victim == v:
                    if am.group("kind") == "assisted":
                        k.assister = a
                    else:
                        k.flash_assister = a
                    break
        elif (bm := RE_BLIND.search(ev)):
            a, v = note(bm, "a"), note(bm, "v")
            if v:
                cur.blinds.append(Blind(tick=tick_of(ts), attacker=a, victim=v,
                                        duration=float(bm.group("dur"))))
        elif (xm := RE_ATTACK.search(ev)):
            a, v = note(xm, "a"), note(xm, "v")
            if v:
                cur.damages.append(Damage(tick=tick_of(ts), attacker=a, victim=v,
                                          dmg=int(xm.group("dmg")), weapon=xm.group("weapon")))
        elif (su := RE_SUICIDE.search(ev)):
            v = note(su, "v")
            if v:
                cur.kills.append(Kill(tick=tick_of(ts), attacker=None, victim=v,
                                      weapon=su.group("weapon")))
        else:
            for tm in RE_TAGGED.finditer(ev):
                note(tm)

    # Resolve damage overrides from round_stats when no per-hit damage exists.
    for md in matches:
        blk = md.damage_override.pop("_round_stats", None)
        has_hits = any(r.damages for r in md.rounds)
        if blk and not has_hits:
            _, players, _ = blk
            md.damage_override = {
                sid: {"damage": int(row.get("dmg", 0)), "util_damage": int(row.get("ud", 0)),
                      "enemies_flashed": int(row.get("ef", 0))}
                for sid, row in players.items()}
        md.__dict__["round_stats_check"] = blk   # kept for validation
    return matches
