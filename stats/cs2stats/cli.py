"""Command-line helpers — test everything without Discord.

  python -m cs2stats.cli show-demo  path/to/match.dem
  python -m cs2stats.cli show-log   path/to/server.log
  python -m cs2stats.cli check      match.dem server.log     # demo vs server scoreboard
  python -m cs2stats.cli import-demos /path/to/MatchZy
  python -m cs2stats.cli import-log  server.log
  python -m cs2stats.cli leaderboard [stat] [min_matches]
"""
from __future__ import annotations

import os
import sys

from .db import DB
from .stats import compute
from .leaderboard import ranked, STATS

DB_PATH = os.environ.get("DB_PATH", "data/stats.db")


def table(players, summary, title):
    print(f"\n{title}  —  {summary['map']}  A {summary['score_a']} : {summary['score_b']} B  "
          f"({summary['rounds']} rounds)")
    hdr = f"{'':2}{'Player':16}{'K':>4}{'D':>4}{'A':>4}{'ADR':>6}{'KAST':>6}{'HS%':>5}" \
          f"{'Open':>6}{'Trd':>4}{'Clutch':>7}{'UD':>5}{'EF':>4}{'RTG':>6}"
    for team in ("A", "B"):
        print(hdr)
        for p in sorted([p for p in players.values() if p.team == team], key=lambda p: -p.rating):
            r = max(p.rounds, 1)
            print(f"{team:2}{p.name[:15]:16}{p.kills:>4}{p.deaths:>4}{p.assists:>4}{p.damage / r:>6.0f}"
                  f"{100 * p.kast_rounds / r:>5.0f}%{100 * p.hs_kills / max(p.kills, 1):>5.0f}"
                  f"{p.entry_wins:>3}/{p.entry_attempts:<2}{p.trade_kills:>4}"
                  f"{p.clutch_wins:>4}/{p.clutch_attempts:<2}{p.util_damage:>5.0f}{p.enemies_flashed:>4}{p.rating:>6.2f}")


def main(argv=None):
    a = argv or sys.argv[1:]
    if not a:
        print(__doc__)
        return
    cmd = a[0]
    if cmd == "show-demo":
        from .parse_demo import parse_demo
        md = parse_demo(a[1])
        P, s = compute(md)
        table(P, s, md.source_name)
    elif cmd == "show-log":
        from .parse_log import parse_log
        for md in parse_log(a[1]):
            P, s = compute(md)
            table(P, s, f"match {md.matchzy_id} ({md.played_at})")
    elif cmd == "check":
        from .parse_demo import parse_demo
        from .parse_log import parse_log
        md = parse_demo(a[1])
        P, s = compute(md)
        logs = [m for m in parse_log(a[2]) if m.map_name == md.map_name and
                (not md.matchzy_id or m.matchzy_id == md.matchzy_id)]
        if not logs:
            raise SystemExit("No matching map in that log.")
        _, srv, _ = logs[-1].round_stats_check
        table(P, s, "demo")
        print("\nServer scoreboard comparison (server's last snapshot is one round before the end):")
        for sid, row in srv.items():
            p = P.get(sid)
            if p:
                print(f"  {p.name[:15]:16} demo K/D/A {p.kills}/{p.deaths}/{p.assists}   "
                      f"server {int(row['kills'])}/{int(row['deaths'])}/{int(row['assists'])}   "
                      f"dmg demo {p.damage:.0f} server {row['dmg']:.0f}")
    elif cmd == "import-demos":
        import asyncio
        from .ingest import DemoWatcher
        db = DB(DB_PATH)

        async def go():
            w = DemoWatcher(db, a[1], on_new_match=lambda mid: asyncio.sleep(0))
            for path in w._candidates():
                if not db.has_demo(os.path.basename(path)):
                    mid, why = await w.process(path, announce=False)
                    print(os.path.basename(path), "->", f"match #{mid}" if mid else f"skipped ({why})")
        asyncio.run(go())
    elif cmd == "import-log":
        from .ingest import import_log
        db = DB(DB_PATH)
        for md, mid, why in import_log(db, a[1]):
            print(md.map_name, md.played_at, "->", f"match #{mid}" if mid else f"skipped ({why})")
    elif cmd == "leaderboard":
        stat = a[1] if len(a) > 1 else "rating"
        mm = int(a[2]) if len(a) > 2 else 1
        rows = ranked(DB(DB_PATH).aggregates(), stat, min_matches=mm)
        print(f"{'#':>2} {'Player':16}{'M':>2}{'W-L':>5}{'R3.0':>6}{'Swing%':>7}"
              f"{'eK-eD':>9}{'eADR':>6}{'KAST':>5} | {'Kill':>5}{'Dmg':>5}{'Surv':>5}{'KAST':>5}{'MK':>5}{'Swg':>5}")
        for i, r in enumerate(rows, 1):
            ekd = f"{r['e_kills']:.0f}-{r['e_deaths']:.0f}"
            rt = f"{r['rating']:>6.2f}" if r['rating'] is not None else f"{'-':>6}"
            sw = f"{r['swing_pr']:>+7.2f}" if r['swing_pr'] is not None else f"{'-':>7}"
            print(f"{i:>2} {r['name'][:15]:16}{r['matches']:>2}{r['wins']:>3}-{r['losses']}{rt}"
                  f"{sw}{ekd:>9}{r['eadr']:>6.1f}{r['kast']:>4.0f}% | "
                  + "".join(f"{r.get('sub_' + k, 0):>5.2f}" for k in
                            ("kills", "damage", "survival", "kast", "multikill", "swing")))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
