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


def _low_priority():
    """Parser process runs at the lowest CPU priority so the game server always wins."""
    try:
        os.nice(19)
    except (AttributeError, OSError):
        pass


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
        self.skipped: set[str] = set()       # this run; also saved in the DB so restarts don't re-read
        self.done: set[str] | None = None    # loaded from the DB on the first pass
        self.pool = ProcessPoolExecutor(max_workers=1, initializer=_low_priority)
        self.pause = 2.0                     # seconds to rest between demos during a backfill

    def _skip(self, path, name, why):
        self.skipped.add(name)
        try:
            self.db.mark_skipped(name, os.path.getsize(path), why)
        except OSError:
            pass

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
            self._skip(path, name, f"parse error: {e}")
            return None, f"parse error: {e}"
        upgrading = self.db.has(name)          # a log backfill exists for this demo
        if upgrading and qualifies(summary, players, self.min_rounds, self.min_team_size) is None:
            self.db.drop_log_version(name)
            announce = False                       # already posted/known; just upgrade quietly
        elif upgrading:
            self._skip(path, name, "kept log version (demo didn't qualify)")
            return None, "kept log version (demo didn't qualify)"
        mid, why = store(self.db, name, mzid, played, summary, players, "demo",
                         self.min_rounds, self.min_team_size, announce=announce)
        if why:
            log.info("skipped %s: %s", name, why)
            self._skip(path, name, why)
        else:
            log.info("imported %s as match #%s", name, mid)
            if announce:
                await self.on_new(mid)
        return mid, why

    async def refresh_one(self, paths: dict) -> bool:
        """Re-read one demo imported by an older version, to fill in newer details
        (who-killed-who, side splits). Same match id; nothing is re-announced."""
        stale = self.db.stale_demos()
        if not stale:
            return False
        name, mid = next(iter(sorted(stale.items(), key=lambda kv: kv[1])))
        path = paths.get(name)
        if not path:
            self.db.mark_details(mid)              # demo deleted; keep what we have
            return True
        loop = asyncio.get_running_loop()
        try:
            _, _, _, summary, players = await loop.run_in_executor(self.pool, analyze_demo, path)
            self.db.refresh_details(mid, summary, players)
            log.info("refreshed details for match #%s from %s", mid, name)
        except Exception:
            log.exception("could not re-read %s", name)
            self.db.mark_details(mid)
        return True

    async def run(self, backfill_announce=False):
        first = True
        while True:
            try:
                now = loop_time()
                if self.done is None:
                    self.done = self.db.demo_keys()
                busy = False
                candidates = self._candidates()
                for path in candidates:
                    name = os.path.basename(path)
                    if name in self.skipped or name in self.done:
                        continue
                    size = os.path.getsize(path)
                    prev = self.sizes.get(name)
                    if prev is None or prev[0] != size:
                        self.sizes[name] = (size, now)
                        if not first:
                            busy = True
                            continue
                    # on first pass, files untouched for a while are already done
                    age = now - os.path.getmtime(path)
                    if (prev and now - prev[1] >= self.stable) or age >= self.stable:
                        await self.process(path, announce=(not first) or backfill_announce)
                        self.done.add(name)                # imported or skipped: never re-read
                        busy = True
                        await asyncio.sleep(self.pause)    # breathe between demos during a backfill
                    elif name not in self.done:
                        busy = True                        # a demo is still being written
                first = False
                # quiet moment: upgrade older imports a few at a time
                if not busy:
                    paths = {os.path.basename(p): p for p in candidates}
                    for _ in range(3):
                        if not await self.refresh_one(paths):
                            break
                        await asyncio.sleep(self.pause)
            except Exception:
                log.exception("watcher loop error")
            await asyncio.sleep(self.poll)


def loop_time():
    import time
    return time.time()
