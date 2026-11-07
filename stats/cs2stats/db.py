"""SQLite storage + leaderboard aggregation."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime

from .stats import PlayerMatch, rating2
from . import rating3 as r3

INT_COLS = ["rounds", "rounds_won", "kills", "deaths", "assists", "hs_kills", "damage",
            "util_damage", "kast_rounds", "entry_attempts", "entry_wins", "trade_kills",
            "traded_deaths", "clutch_attempts", "clutch_wins", "k2", "k3", "k4", "k5", "aces",
            "enemies_flashed", "team_flashed", "flash_assists", "teamkills", "saved_lost"]
REAL_COLS = ["blind_time", "rating", "e_kills", "e_deaths", "e_damage", "swing", "mk_points"]

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS matches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_key TEXT UNIQUE,          -- demo filename or log key; prevents double-imports
  matchzy_id TEXT,
  map TEXT, played_at TEXT, imported_at TEXT,
  rounds INTEGER, score_a INTEGER, score_b INTEGER,
  source TEXT,                     -- 'demo' or 'log'
  team_a_name TEXT, team_b_name TEXT
);
CREATE TABLE IF NOT EXISTS player_match (
  match_id INTEGER REFERENCES matches(id) ON DELETE CASCADE,
  steamid TEXT, name TEXT, team TEXT, result TEXT, clutches TEXT,
  {", ".join(f"{c} INTEGER" for c in INT_COLS)},
  {", ".join(f"{c} REAL" for c in REAL_COLS)},
  PRIMARY KEY (match_id, steamid)
);
CREATE TABLE IF NOT EXISTS players (
  steamid TEXT PRIMARY KEY, name TEXT, discord_id TEXT UNIQUE
);
"""


class DB:
    def __init__(self, path: str):
        self.path = path
        with self.conn() as c:
            c.executescript(SCHEMA)
            have = {r["name"] for r in c.execute("PRAGMA table_info(player_match)")}
            for col in INT_COLS + REAL_COLS:              # migrate older databases
                if col not in have:
                    kind = "REAL" if col in REAL_COLS else "INTEGER"
                    c.execute(f"ALTER TABLE player_match ADD COLUMN {col} {kind} DEFAULT 0")
            have_m = {r["name"] for r in c.execute("PRAGMA table_info(matches)")}
            for col in ("team_a_name", "team_b_name", "round_log"):
                if col not in have_m:
                    c.execute(f"ALTER TABLE matches ADD COLUMN {col} TEXT")
            if "announced" not in have_m:                  # 0 = Discord scorecard not posted yet
                c.execute("ALTER TABLE matches ADD COLUMN announced INTEGER DEFAULT 1")
            if "linked_at" not in {r["name"] for r in c.execute("PRAGMA table_info(players)")}:
                c.execute("ALTER TABLE players ADD COLUMN linked_at TEXT")
            c.execute("CREATE INDEX IF NOT EXISTS pm_steamid ON player_match(steamid)")

    def baseline(self):
        """Group baseline for Rating 3.0: every player-map in the database."""
        with self.conn() as c:
            return r3.baseline([dict(r) for r in c.execute("SELECT * FROM player_match")])

    def _rate(self, rows, base=None):
        base = self.baseline() if base is None else base
        for r in rows:
            r["rating2"] = r.get("rating")
            r["rating"], subs = r3.rating3(r, base)
            r["rating_kind"] = "3.0" if r["rating"] is not None else "2.0"
            if r["rating"] is None:
                r["rating"] = r["rating2"]
            r.update({f"sub_{k}": v for k, v in subs.items()})
            r["swing_pr"] = 100 * (r.get("swing") or 0) / max(r.get("rounds") or 1, 1)
        return rows

    @contextmanager
    def conn(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def has(self, source_key: str) -> bool:
        with self.conn() as c:
            return c.execute("SELECT 1 FROM matches WHERE source_key=?", (source_key,)).fetchone() is not None

    def has_demo(self, source_key: str) -> bool:
        """True only if this key was imported from the demo itself (not a log backfill)."""
        with self.conn() as c:
            return c.execute("SELECT 1 FROM matches WHERE source_key=? AND source='demo'",
                             (source_key,)).fetchone() is not None

    def drop_log_version(self, source_key: str):
        with self.conn() as c:
            c.execute("DELETE FROM matches WHERE source_key=? AND source='log'", (source_key,))

    def has_matchzy(self, matchzy_id: str, map_name: str) -> bool:
        with self.conn() as c:
            return c.execute("SELECT 1 FROM matches WHERE matchzy_id=? AND map=?",
                             (matchzy_id, map_name)).fetchone() is not None

    def save_match(self, source_key, summary, players: dict[str, PlayerMatch], *,
                   matchzy_id=None, played_at=None, source="demo", team_names=(None, None),
                   announce=False) -> int:
        with self.conn() as c:
            cur = c.execute(
                "INSERT INTO matches (source_key, matchzy_id, map, played_at, imported_at, rounds,"
                " score_a, score_b, source, team_a_name, team_b_name, round_log, announced)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (source_key, matchzy_id, summary["map"], played_at,
                 datetime.now().isoformat(timespec="seconds"), summary["rounds"],
                 summary["score_a"], summary["score_b"], source, *team_names,
                 summary.get("round_log"), 0 if announce else 1))
            mid = cur.lastrowid
            cols = ["steamid", "name", "team", "result", "clutches"] + INT_COLS + REAL_COLS
            for pm in players.values():
                row = pm.as_row()
                c.execute(f"INSERT INTO player_match (match_id, {', '.join(cols)}) VALUES "
                          f"(?, {', '.join('?' * len(cols))})", [mid] + [row[k] for k in cols])
                c.execute("INSERT INTO players (steamid, name) VALUES (?, ?) "
                          "ON CONFLICT(steamid) DO UPDATE SET name=excluded.name", (pm.steamid, pm.name))
            return mid

    def delete_match(self, match_id: int):
        with self.conn() as c:
            c.execute("DELETE FROM matches WHERE id=?", (match_id,))

    # ---------- players ----------
    def link(self, discord_id: str, steamid: str):
        with self.conn() as c:
            c.execute("UPDATE players SET discord_id=NULL WHERE discord_id=?", (discord_id,))
            now = datetime.now().isoformat(timespec="seconds")
            c.execute("INSERT INTO players (steamid, discord_id, linked_at) VALUES (?, ?, ?) "
                      "ON CONFLICT(steamid) DO UPDATE SET discord_id=excluded.discord_id, "
                      "linked_at=excluded.linked_at", (steamid, discord_id, now))

    def steamid_for_discord(self, discord_id: str):
        with self.conn() as c:
            r = c.execute("SELECT steamid FROM players WHERE discord_id=?", (discord_id,)).fetchone()
            return r["steamid"] if r else None

    def find_player(self, query: str):
        with self.conn() as c:
            r = c.execute("SELECT steamid, name FROM players WHERE steamid=?", (query,)).fetchone()
            if r:
                return dict(r)
            r = c.execute("SELECT steamid, name FROM players WHERE lower(name)=lower(?)", (query,)).fetchone() \
                or c.execute("SELECT steamid, name FROM players WHERE lower(name) LIKE lower(?) "
                             "ORDER BY length(name)", (f"%{query}%",)).fetchone()
            return dict(r) if r else None

    def player_names(self, prefix: str = "", limit=25):
        with self.conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT steamid, name FROM players WHERE lower(name) LIKE lower(?) ORDER BY name LIMIT ?",
                (f"%{prefix}%", limit))]

    # ---------- matches ----------
    def match(self, match_id=None):
        with self.conn() as c:
            m = c.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone() if match_id else \
                c.execute("SELECT * FROM matches ORDER BY played_at DESC, id DESC LIMIT 1").fetchone()
            if not m:
                return None, []
            ps = c.execute("SELECT * FROM player_match WHERE match_id=?", (m["id"],)).fetchall()
        players = self._rate([dict(p) for p in ps])
        return dict(m), sorted(players, key=lambda p: -p["rating"])

    def recent_matches(self, limit=10, offset=0):
        with self.conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM matches ORDER BY played_at DESC, id DESC LIMIT ? OFFSET ?", (limit, offset))]

    def count_matches(self) -> int:
        with self.conn() as c:
            return c.execute("SELECT COUNT(*) FROM matches").fetchone()[0]

    # ---------- Discord announce queue ----------
    def pending_announcements(self):
        with self.conn() as c:
            return [r[0] for r in c.execute("SELECT id FROM matches WHERE announced=0 ORDER BY id")]

    def mark_announced(self, match_id: int):
        with self.conn() as c:
            c.execute("UPDATE matches SET announced=1 WHERE id=?", (match_id,))

    def discord_links(self) -> dict:
        with self.conn() as c:
            return {r["steamid"]: r["discord_id"] for r in
                    c.execute("SELECT steamid, discord_id FROM players WHERE discord_id IS NOT NULL")}

    def unlink(self, discord_id: str):
        with self.conn() as c:
            c.execute("UPDATE players SET discord_id=NULL WHERE discord_id=?", (discord_id,))

    def player_history(self, steamid, limit=10):
        with self.conn() as c:
            return self._rate([dict(r) for r in c.execute(
                "SELECT pm.*, m.map, m.played_at, m.score_a, m.score_b, m.rounds AS match_rounds, "
                "m.team_a_name, m.team_b_name FROM player_match pm "
                "JOIN matches m ON m.id = pm.match_id WHERE steamid=? "
                "ORDER BY m.played_at DESC, m.id DESC LIMIT ?", (steamid, limit))])

    def player(self, steamid):
        with self.conn() as c:
            r = c.execute("SELECT * FROM players WHERE steamid=?", (steamid,)).fetchone()
            return dict(r) if r else None

    # ---------- aggregates ----------
    def aggregates(self, since: str | None = None, map_name: str | None = None):
        where, args = [], []
        if since:
            where.append("m.played_at >= ?"); args.append(since)
        if map_name:
            where.append("m.map = ?"); args.append(map_name)
        w = ("WHERE " + " AND ".join(where)) if where else ""
        sums = ", ".join(f"SUM(pm.{c}) AS {c}" for c in INT_COLS + [c for c in REAL_COLS if c != "rating"])
        sql = (f"SELECT pm.steamid, p.name, COUNT(*) AS matches, "
               f"SUM(pm.result='W') AS wins, SUM(pm.result='L') AS losses, "
               f"AVG(pm.rating) AS avg_match_rating, {sums}, "
               f"MIN(pm.e_kills > 0 OR pm.e_deaths > 0 OR pm.swing != 0) AS r3_complete "
               f"FROM player_match pm JOIN matches m ON m.id=pm.match_id "
               f"JOIN players p ON p.steamid=pm.steamid {w} GROUP BY pm.steamid")
        with self.conn() as c:
            rows = [dict(r) for r in c.execute(sql, args)]
        base = self.baseline()
        rows = [derive(r) for r in rows]
        for r in rows:
            r["rating2"] = r["rating"]
            r3v, subs = r3.rating3(r, base)
            r["rating_kind"] = "3.0" if r3v is not None else "2.0"
            if r3v is not None:
                r["rating"] = r3v
            r.update({f"sub_{k}": v for k, v in subs.items()})
        return rows


def derive(r: dict) -> dict:
    """Turn summed counters into rates."""
    rounds = max(r["rounds"] or 0, 1)
    k, d = r["kills"] or 0, r["deaths"] or 0
    r["kd"] = k / max(d, 1)
    r["kpr"] = k / rounds
    r["adr"] = (r["damage"] or 0) / rounds
    r["kast"] = 100 * (r["kast_rounds"] or 0) / rounds
    r["hs_pct"] = 100 * (r["hs_kills"] or 0) / max(k, 1)
    r["win_pct"] = 100 * (r["wins"] or 0) / max(r["matches"], 1)
    r["rating"] = rating2(k, d, r["assists"] or 0, r["kast_rounds"] or 0, r["damage"] or 0, rounds)
    r["entry_rate"] = 100 * (r["entry_attempts"] or 0) / rounds
    r["entry_win_pct"] = 100 * (r["entry_wins"] or 0) / max(r["entry_attempts"] or 0, 1)
    r["trade_kpr"] = 100 * (r["trade_kills"] or 0) / rounds        # per 100 rounds
    r["traded_pct"] = 100 * (r["traded_deaths"] or 0) / max(d, 1)
    r["clutch_win_pct"] = 100 * (r["clutch_wins"] or 0) / max(r["clutch_attempts"] or 0, 1)
    r["multikill_pct"] = 100 * sum(r[f"k{i}"] or 0 for i in range(2, 6)) / rounds
    r["util_adr"] = (r["util_damage"] or 0) / rounds
    r["flashed_pr"] = (r["enemies_flashed"] or 0) / rounds
    r["flash_assists_pr"] = 100 * (r["flash_assists"] or 0) / rounds
    r["swing_pr"] = 100 * (r.get("swing") or 0) / rounds        # % win-probability per round
    r["ekd"] = (r.get("e_kills") or 0) / max(r.get("e_deaths") or 0, 1)
    r["eadr"] = (r.get("e_damage") or 0) / rounds
    return r
