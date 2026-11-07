"""Turn a demo/log into stored stats, and watch MatchZy's demo folder."""
from __future__ import annotations

import asyncio
import logging
import os
import re
from concurrent.futures import ProcessPoolExecutor

from .db import DB
from .model import MatchData
from .stats import compute

log = logging.getLogger("cs2stats.ingest")
TEAMS_IN_NAME = re.compile(r"_team_(.+)_vs_team_(.+)\.dem$")


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def team_names(source_name: str, players) -> tuple:
    """MatchZy names teams `team_<captain>`. Map those onto our A/B teams."""
    m = TEAMS_IN_NAME.search(source_name or "")
    if not m:
        return (None, None)
    out = {"A": None, "B": None}
    for raw in m.groups():
        cap = _norm(raw)
        for pm in players.values():
            if _norm(pm.name) == cap and pm.team in out and not out[pm.team]:
                out[pm.team] = f"Team {pm.name}"
                break
    return (out["A"], out["B"])


ALLOWED_MODES = {m.strip() for m in os.environ.get("ALLOWED_MODES", "competitive").split(",")}
WIN_ROUNDS = int(os.environ.get("WIN_ROUNDS", "13"))   # MR12; set 9 for MR8 short matches


def qualifies(summary, players, min_rounds, min_team_size) -> str | None:
    """Return a reason to skip, or None if this is a real, finished competitive match."""
    mode = summary.get("game_mode")
    if mode and mode not in ALLOWED_MODES:
        return f"game mode '{mode}'"
    if summary["rounds"] < min_rounds:
        return f"only {summary['rounds']} rounds"
    buys = summary.get("purchases")
    if buys is not None and buys < summary["rounds"]:
        return f"looks like retakes ({buys} purchases in {summary['rounds']} rounds)"
    hi, lo = max(summary["score_a"], summary["score_b"]), min(summary["score_a"], summary["score_b"])
    if hi < WIN_ROUNDS or (hi + lo > 2 * (WIN_ROUNDS - 1) and hi - lo < 2):
        return f"unfinished ({summary['score_a']}:{summary['score_b']})"
    sizes = {t: sum(1 for p in players.values() if p.team == t and p.rounds >= summary['rounds'] / 2)
             for t in ("A", "B")}
    if min(sizes.values()) < min_team_size:
        return f"team sizes {sizes['A']}v{sizes['B']}"
    return None


def analyze_demo(path: str):
    """Runs in a worker process (demo parsing is CPU-heavy)."""
    from .parse_demo import parse_demo
    md = parse_demo(path)
    players, summary = compute(md)
    return md.source_name, md.matchzy_id, md.played_at, summary, players


def store(db: DB, source_key, matchzy_id, played_at, summary, players, source,
          min_rounds, min_team_size, announce=False):
    why = qualifies(summary, players, min_rounds, min_team_size)
    if why:
        return None, why
    if matchzy_id and source == "log" and db.has_matchzy(matchzy_id, summary["map"]):
        return None, "already imported from demo"
    mid = db.save_match(source_key, summary, players, matchzy_id=matchzy_id, played_at=played_at,
                        source=source, team_names=team_names(source_key, players),
                        announce=announce)
    return mid, None


def import_log(db: DB, path: str, min_rounds=10, min_team_size=4):
    from .parse_log import parse_log
    results = []
    for md in parse_log(path):
        key = f"log:{md.matchzy_id or ''}:{md.map_name}:{md.played_at}"
        if db.has(key) or (md.source_name.endswith(".dem") and db.has(md.source_name)):
            results.append((md, None, "already imported"))
            continue
        players, summary = compute(md)
        mid, why = store(db, key if not md.source_name.endswith(".dem") else md.source_name,
                         md.matchzy_id, md.played_at, summary, players, "log", min_rounds, min_team_size)
        results.append((md, mid, why))
    return results


class DemoWatcher:
    """Polls a folder for finished .dem files. A demo counts as finished once
    its size hasn't changed for `stable_seconds` (MatchZy keeps writing for
    ~15s after the map ends)."""

    def __init__(self, db: DB, demo_dir: str, on_new_match, *, poll=20, stable_seconds=45,
                 min_rounds=10, min_team_size=4):
        self.db, self.dir, self.on_new = db, demo_dir, on_new_match
        self.poll, self.stable = poll, stable_seconds
        self.min_rounds, self.min_team_size = min_rounds, min_team_size
        self.sizes: dict[str, tuple[int, float]] = {}
        self.skipped: set[str] = set()
        self.pool = ProcessPoolExecutor(max_workers=1)

    def _candidates(self):
        if not os.path.isdir(self.dir):
            return []
        out = []
        for root, _, files in os.walk(self.dir):
            for f in files:
                if f.endswith(".dem"):
                    out.append(os.path.join(root, f))
        return sorted(out, key=os.path.getmtime)

    async def process(self, path, announce=True):
        name = os.path.basename(path)
        loop = asyncio.get_running_loop()
        try:
            source_name, mzid, played, summary, players = await loop.run_in_executor(
                self.pool, analyze_demo, path)
        except Exception as e:
            log.exception("failed to parse %s", name)
            self.skipped.add(name)
            return None, f"parse error: {e}"
        upgrading = self.db.has(name)          # a log backfill exists for this demo
        if upgrading and qualifies(summary, players, self.min_rounds, self.min_team_size) is None:
            self.db.drop_log_version(name)
            announce = False                       # already posted/known; just upgrade quietly
        elif upgrading:
            self.skipped.add(name)
            return None, "kept log version (demo didn't qualify)"
        mid, why = store(self.db, name, mzid, played, summary, players, "demo",
                         self.min_rounds, self.min_team_size, announce=announce)
        if why:
            log.info("skipped %s: %s", name, why)
            self.skipped.add(name)
        else:
            log.info("imported %s as match #%s", name, mid)
            if announce:
                await self.on_new(mid)
        return mid, why

    async def run(self, backfill_announce=False):
        first = True
        while True:
            try:
                now = loop_time()
                for path in self._candidates():
                    name = os.path.basename(path)
                    if name in self.skipped or self.db.has_demo(name):
                        continue
                    size = os.path.getsize(path)
                    prev = self.sizes.get(name)
                    if prev is None or prev[0] != size:
                        self.sizes[name] = (size, now)
                        if not first:
                            continue
                    # on first pass, files untouched for a while are already done
                    age = now - os.path.getmtime(path)
                    if (prev and now - prev[1] >= self.stable) or age >= self.stable:
                        await self.process(path, announce=(not first) or backfill_announce)
                first = False
            except Exception:
                log.exception("watcher loop error")
            await asyncio.sleep(self.poll)


def loop_time():
    import time
    return time.time()
