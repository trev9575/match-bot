"""Stats service: watches MatchZy's demo folder, stores stats in SQLite, and
serves the leaderboard website plus a small JSON API for the Discord bot.

Standard library only (besides the demo parser), so the container stays small.

Public (read-only) endpoints — used by the website:
  GET  /api/summary
  GET  /api/leaderboard?period=all|30|7&map=de_x
  GET  /api/players?q=name
  GET  /api/players/<steamid>
  GET  /api/matches?limit=20&offset=0
  GET  /api/matches/<id>
  GET  /api/ranks                          rank bands + which custom badge images exist
  GET  /api/join-check?host=<cs2 host>     {"home": true} if the visitor is on the server's own network
  GET  /api/seasons                         every season with its final (or live) Elo standings
  GET  /api/achievements                    achievement catalog with how many players have each
  GET  /api/superlatives?season=N           duos, rivalries
  GET  /api/recap?days=7                    the last week (or since=&until=)
  GET  /api/maps                            maps we've played (for map votes)
  GET  /ach/<id>.svg|png                    achievement badges

Bot-only endpoints — need "Authorization: Bearer $STATS_API_KEY":
  GET  /api/announcements                 matches waiting to be posted to Discord
  POST /api/announcements/<id>/done
  GET  /api/discord/<discord_id>          -> linked steamid
  POST /api/link     {"discord_id", "steamid", "force"}   force = admin taking over an existing link
  GET  /api/links                         every player with match count + linked Discord id
  POST /api/unlink   {"discord_id"}
  POST /api/rescan
  POST /api/discord-ratings {"discord_ids": [...]}  -> Elo for linked members (team balancing)
  POST /api/predict  {"team_a": [discord ids], "team_b": [...]}  -> win odds + Elo at stake
  GET/POST /api/kv/<key>  {"value": "..."}  small store for the bot (recap / season posts)
  POST /api/matchzy-event   MatchZy's live events (matchzy_remote_log_url), header Authorization: Bearer <key>
  GET  /api/events?after=N  those events for the bot (e.g. map_result -> move everyone back to the lobby)
  DELETE /api/matches/<id>
"""
from __future__ import annotations

import asyncio
import collections
import hmac
import ipaddress
import socket
import json
import logging
import mimetypes
import os
import re
import shutil
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from . import community
from . import elo as elo_mod
from . import icons
from .db import DB
from .ingest import DemoWatcher
from .leaderboard import with_categories

log = logging.getLogger("cs2stats.server")
WEB_DIR = os.path.join(os.path.dirname(__file__), "web")
STEAMID = re.compile(r"^7656119\d{10}$")


def env_int(name, default):
    v = os.environ.get(name, "").split("#")[0].strip()
    return int(v) if v else default


def env_bool(name):
    return os.environ.get(name, "").split("#")[0].strip().lower() in ("1", "true", "yes")


# ----------------------------------------------------------------------------- helpers

def _clean(v):
    """Round floats so responses stay small; drop NaN."""
    if isinstance(v, float):
        return None if v != v else round(v, 4)
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    return v


def _since(period):
    if period in (None, "", "all"):
        return None
    try:
        days = int(period)
    except ValueError:
        return None
    return (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")


_dns_cache: dict[str, tuple[float, set]] = {}


def resolve(host: str) -> set:
    """IPs for a hostname, cached for a minute."""
    hit = _dns_cache.get(host)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    try:
        ips = {ai[4][0] for ai in socket.getaddrinfo(host, None)}
    except OSError:
        ips = set()
    _dns_cache[host] = (time.time(), ips)
    return ips


def highlights(players: list[dict]) -> list[dict]:
    """Match awards shown on the scorecard and the match page."""
    if not players:
        return []
    out = []

    def add(key, icon, title, p, text):
        out.append({"key": key, "icon": icon, "title": title, "steamid": p["steamid"],
                    "name": p["name"], "text": text})

    def best(k):
        return max(players, key=lambda p: p.get(k) or 0)

    mvp = best("rating") if any(p.get("rating") is not None for p in players) else best("kills")
    add("mvp", "⭐", "MVP", mvp, (f"{mvp['rating']:.2f} rating · " if mvp.get("rating") is not None else "")
        + f"{mvp['kills']}-{mvp['deaths']}")
    ent = best("entry_wins")
    if ent["entry_wins"]:
        add("entry", "🚪", "Entry king", ent, f"won {ent['entry_wins']}/{ent['entry_attempts']} opening duels")
    clutch = None
    for p in players:
        for c in (p.get("clutches") or "").split(","):
            m = re.match(r"1v(\d+) W", c.strip())
            if m and (not clutch or int(m.group(1)) > clutch[1]):
                clutch = (p, int(m.group(1)))
    if clutch:
        add("clutch", "🧊", "Clutch", clutch[0], f"won a 1v{clutch[1]}")
    for p in players:
        if p.get("aces"):
            add("ace", "💥", "ACE", p, "killed the whole enemy team in a round" +
                (f" ×{p['aces']}" if p["aces"] > 1 else ""))
        elif p.get("k5"):
            add("5k", "🔥", "5K", p, "five kills in a round")
    ut = best("util_damage")
    if ut["util_damage"]:
        add("util", "🧨", "Nade merchant", ut, f"{ut['util_damage']} utility damage")
    fl = best("enemies_flashed")
    if fl["enemies_flashed"]:
        add("flash", "⚡", "Flash god", fl, f"blinded {fl['enemies_flashed']} enemies")
    tf = best("team_flashed")
    if tf["team_flashed"] >= 4:
        add("teamflash", "🙃", "Friendly flasher", tf, f"blinded teammates {tf['team_flashed']}×")
    return out


# ----------------------------------------------------------------------------- service

class StatsService:
    def __init__(self):
        self.db_path = os.environ.get("DB_PATH", "data/stats.db")
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        backfill = os.environ.get("BACKFILL_DB", "backfill/stats.db")
        if not os.path.exists(self.db_path) and os.path.exists(backfill):
            shutil.copyfile(backfill, self.db_path)        # first start: seed with the log backfill
            log.info("seeded %s from %s", self.db_path, backfill)
        self.db = DB(self.db_path)
        self.api_key = os.environ.get("STATS_API_KEY", "").strip()
        self.min_matches = env_int("LEADERBOARD_MIN_MATCHES", 2)
        self.watcher = DemoWatcher(
            self.db, os.environ.get("DEMO_DIR", "/demos"), self._on_new,
            poll=env_int("POLL_SECONDS", 20), stable_seconds=env_int("STABLE_SECONDS", 45),
            min_rounds=env_int("MIN_ROUNDS", 10), min_team_size=env_int("MIN_TEAM_SIZE", 4))
        self.loop = asyncio.new_event_loop()
        self.rank_dir = os.environ.get("RANK_IMAGE_DIR", os.path.join(os.path.dirname(self.db_path) or ".", "ranks"))
        self._elo = (None, None)
        self._elo_lock = threading.Lock()
        self._comm = (None, None)
        self.events = collections.deque(maxlen=100)       # recent MatchZy events, newest last
        self.event_seq = 0
        self._ev_lock = threading.Lock()

    KEEP_EVENTS = {"going_live", "map_result", "series_end", "series_start", "side_picked", "map_picked"}

    def add_event(self, body: dict):
        """A MatchZy remote-log event. Only a few small fields are kept."""
        name = str(body.get("event", ""))
        if name not in self.KEEP_EVENTS:
            return None
        ev = {"event": name, "matchid": body.get("matchid"), "map_number": body.get("map_number"),
              "at": datetime.now().isoformat(timespec="seconds")}
        for k in ("team1", "team2", "winner"):
            v = body.get(k)
            if isinstance(v, dict):
                ev[k] = {x: v.get(x) for x in ("name", "score", "team", "side") if x in v}
        with self._ev_lock:
            self.event_seq += 1
            ev["id"] = self.event_seq
            self.events.append(ev)
        log.info("MatchZy event: %s", name)
        return ev

    def events_after(self, after: int):
        with self._ev_lock:
            return {"events": [e for e in self.events if e["id"] > after], "last": self.event_seq}

    # ---- Elo: replayed from the match history, cached until matches change ----
    def elo_full(self):
        v = (self.db.version(), datetime.now().strftime("%Y-%m-%d %H"))   # hourly: idle RD / season roll
        with self._elo_lock:
            if self._elo[0] != v:
                self._elo = (v, elo_mod.compute_full(self.db.elo_rows()))
            return self._elo[1]

    def elo(self):
        full = self.elo_full()
        return full["players"], full["per_match"]

    def comm(self):
        """Rows, kill pairs, names and achievements; cached until matches change."""
        full = self.elo_full()
        with self._elo_lock:
            if self._comm[0] is not full:
                rows = self.db.all_rows()
                data = {"rows": rows, "pairs": self.db.kill_pairs(), "names": self.db.names()}
                data["ach"] = community.achievements(rows, full)
                data["player_count"] = len({r["steamid"] for r in rows})
                self._comm = (full, data)
            return self._comm[1]

    def season_param(self, value):
        """'current', a number, or None -> season number or None."""
        if value in (None, "", "all"):
            return None
        if value == "current":
            return elo_mod.season_of(datetime.now().isoformat(timespec="seconds"))
        return int(value)

    def _with_elo(self, rows, match_id=None):
        """Add rank + Elo. Until a player finishes placement their Elo is hidden (None), the way
        CS:GO hid ranks; it's kept as elo_sort so lists can still be ordered by it."""
        players, per_match = self.elo()
        for r in rows:
            info = dict(players.get(r["steamid"], {}))
            placed = info.get("placed", False)
            if info:
                info["elo_sort"] = info["elo"]
                if not placed:
                    info["elo"] = None
            r.update(info)
            mid = match_id or r.get("match_id")
            if mid is not None:
                # per-match Elo changes show once lifetime placement is done (a season re-placement
                # hides the current rank, not old results)
                pm = per_match.get((mid, r["steamid"]), {})
                done = info.get("elo_matches", 0) >= elo_mod.PLACEMENT_MATCHES
                r.update(pm if done else {k: None for k in pm})
        return rows

    async def _on_new(self, match_id):
        log.info("match #%s queued for Discord", match_id)

    def start_watcher(self):
        def run():
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self.watcher.run(backfill_announce=env_bool("BACKFILL_ANNOUNCE")))
        threading.Thread(target=run, name="demo-watcher", daemon=True).start()

    def rescan(self) -> int:
        async def go():
            self.watcher.skipped.clear()
            self.db.clear_skipped()                    # admin asked: give skipped demos another look
            self.watcher.done = None
            new = 0
            for path in self.watcher._candidates():
                name = os.path.basename(path)
                if time.time() - os.path.getmtime(path) < self.watcher.stable:
                    continue                                   # still being recorded
                if not self.db.has_demo(name):
                    mid, _ = await self.watcher.process(path, announce=False)
                    new += bool(mid)
            return new
        return asyncio.run_coroutine_threadsafe(go(), self.loop).result(timeout=600)

    # ---- read models ----
    def leaderboard(self, period=None, map_name=None, season=None):
        if season:
            start, end = elo_mod.season_bounds(season)
            rows = with_categories(self.db.aggregates(since=start, until=end, map_name=map_name))
            info = elo_mod.season_info(season)
            if info["finished"]:
                # a finished season is ranked by where everyone ended it
                final = self.elo_full()["season_final"].get(season, {})
                for r in rows:
                    f = final.get(r["steamid"], {})
                    placed = f.get("placed", False)
                    r.update({k: f.get(k) for k in ("rank", "rank_name", "rank_short", "rank_color")})
                    r.update(placed=placed, elo_sort=f.get("elo"), elo=f.get("elo") if placed else None)
                rows.sort(key=lambda r: (not r.get("placed"), -(r.get("elo_sort") or 0)))
                for i, r in enumerate(rows, 1):
                    r["position"] = i
                return rows
            rows = self._with_elo(rows)
        else:
            rows = self._with_elo(with_categories(self.db.aggregates(since=_since(period), map_name=map_name)))
        # ranked by Elo: placed players first, then players still in placement (by their hidden Elo)
        rows.sort(key=lambda r: (not r.get("placed"), -(r.get("elo_sort") or 0)))
        for i, r in enumerate(rows, 1):
            r["position"] = i
        return rows

    def summary(self):
        rows = self.leaderboard()
        ms = self.db.recent_matches(10000)
        rated = [r for r in rows if r["rating"] is not None]
        qualified = [r for r in rated if r["matches"] >= self.min_matches] or rated
        ranked = sorted((r for r in rows if r.get("rank")), key=lambda r: -r["elo"])
        top_k = max(rows, key=lambda r: r["kills"] or 0) if rows else None
        maps = sorted({m["map"] for m in ms})
        return {
            "matches": len(ms),
            "rounds": sum(m["rounds"] or 0 for m in ms),
            "players": len(rows),
            "first_played": min((m["played_at"] for m in ms if m["played_at"]), default=None),
            "last_played": max((m["played_at"] for m in ms if m["played_at"]), default=None),
            "leader": {"steamid": qualified[0]["steamid"], "name": qualified[0]["name"],
                       "rating": qualified[0]["rating"]} if qualified else None,
            "top_killer": {"steamid": top_k["steamid"], "name": top_k["name"],
                           "kills": top_k["kills"]} if top_k else None,
            "top_elo": {k: ranked[0][k] for k in ("steamid", "name", "elo", "rank_name", "rank_short",
                                                  "rank_color", "rank")} if ranked else None,
            "maps": maps,
            "min_matches": self.min_matches,
        }

    def player(self, steamid):
        rows = self.leaderboard()
        agg = next((r for r in rows if r["steamid"] == steamid), None)
        if not agg:
            return None
        history = self._with_elo(self.db.player_history(steamid, limit=500))
        by_map = []
        for m in sorted({h["map"] for h in history}):
            r = next((x for x in self.db.aggregates(map_name=m) if x["steamid"] == steamid), None)
            if r:
                by_map.append({"map": m, **{k: r[k] for k in (
                    "matches", "wins", "losses", "rating", "kd", "adr", "kast", "kills", "deaths")}})
        info = self.db.player(steamid) or {}
        c = self.comm()
        full = self.elo_full()
        odds = full["match_odds"]
        for h in history:
            if h["match_id"] in odds:
                h["win_prob"] = odds[h["match_id"]] if h["team"] == "A" else 1 - odds[h["match_id"]]
        seasons = []
        for n, st in community.season_standings(full).items():
            snap = full["season_final"].get(n, {}).get(steamid)
            if snap:
                pos = next((i for i, p in enumerate(st["players"], 1) if p["steamid"] == steamid), None)
                seasons.append({**{k: st[k] for k in ("season", "name", "start", "end", "finished", "current")},
                                "matches": snap["season_matches"], "position": pos,
                                **({k: snap[k] for k in ("elo", "rank", "rank_name", "rank_short", "rank_color")}
                                   if snap["placed"] else {"elo": None, "rank": 0, "rank_name": "Unranked"})})
        return {"player": agg, "history": history, "maps": by_map,
                "linked": bool(info.get("discord_id")), "group_size": len(rows),
                "achievements": community.player_achievements(c["ach"], steamid, c["player_count"]),
                "head_to_head": community.head_to_head(steamid, c["rows"], c["pairs"], c["names"]),
                "openings": community.openings(steamid, c["rows"]),
                "seasons": seasons}

    def match(self, match_id):
        m, ps = self.db.match(match_id)
        if not m:
            return None
        full = self.elo_full()
        p_a = full["match_odds"].get(m["id"])
        odds = None
        if p_a is not None:
            winner = "A" if (m["score_a"] or 0) > (m["score_b"] or 0) else "B" if (m["score_b"] or 0) > (m["score_a"] or 0) else None
            odds = {"win_prob_a": p_a, "win_prob_b": 1 - p_a,
                    "upset": bool(winner) and (p_a if winner == "A" else 1 - p_a) < 0.4}
        c = self.comm()
        names = c["names"]
        unlocks = [{**community.ACH[aid], "steamid": sid, "player": names.get(sid, sid)}
                   for sid, aid in c["ach"]["match_unlocks"].get(m["id"], [])]
        return {"match": m, "players": self._with_elo(ps, match_id=m["id"]), "highlights": highlights(ps),
                "odds": odds, "achievements": unlocks}

    # ---- community ----
    def seasons(self):
        full = self.elo_full()
        st = community.season_standings(full)
        cur = full["season"]
        if cur not in st:
            st[cur] = {**elo_mod.season_info(cur), "players": []}
        names = self.db.names()
        players = full["players"]
        out = []
        for n in sorted(st, reverse=True):
            s = st[n]
            if s["current"]:
                # live standings: current, placed players who have played this season
                ranked = [{"steamid": sid, **p} for sid, p in players.items()
                          if p["placed"] and not p["inactive"] and p.get("season_matches")]
                ranked.sort(key=lambda p: -p["elo"])
                s = {**s, "players": ranked}
            out.append({**{k: s[k] for k in ("season", "name", "start", "end", "finished", "current")},
                        "players": [{"steamid": p["steamid"], "name": names.get(p["steamid"], p["steamid"]),
                                     **{k: p.get(k) for k in ("elo", "rank", "rank_name", "rank_short",
                                                              "rank_color", "season_matches")}}
                                    for p in s["players"][:20]]})
        return {"seasons": out, "current": cur, "season_months": elo_mod.SEASON_MONTHS,
                "carry": elo_mod.SEASON_CARRY, "season_placement": elo_mod.SEASON_PLACEMENT}

    def superlatives(self, season=None, period=None):
        c = self.comm()
        rows, pairs = c["rows"], c["pairs"]
        since = until = None
        if season:
            since, until = elo_mod.season_bounds(season)
        elif _since(period):
            since = _since(period)
        if since:
            rows = [r for r in rows if r["played_at"] and r["played_at"] >= since and (not until or r["played_at"] < until)]
            ids = {r["match_id"] for r in rows}
            pairs = [p for p in pairs if p["match_id"] in ids]
        return community.superlatives(rows, pairs, c["names"])

    def recap(self, since=None, until=None, days=7):
        now = datetime.now()
        until = until or now.isoformat(timespec="seconds")
        since = since or (datetime.fromisoformat(until) - timedelta(days=days)).isoformat(timespec="seconds")
        c = self.comm()
        return community.recap(c["rows"], c["pairs"], self.elo_full(), c["ach"], c["names"], since, until)

    def predict(self, team_a, team_b):
        """Teams as Discord ids (or SteamIDs). Unlinked members count as brand-new players."""
        links = {d: s for s, d in self.db.discord_links().items()}
        names = self.db.names()

        def sid(x):
            x = str(x)
            return x if STEAMID.match(x) else links.get(x, f"unlinked:{x}")
        a, b = [sid(x) for x in team_a], [sid(x) for x in team_b]
        pr = elo_mod.predict(self.elo_full(), a, b)
        back = {sid(x): str(x) for x in list(team_a) + list(team_b)}
        players = []
        for s, p in pr["players"].items():
            players.append({"id": back.get(s, s), "steamid": None if s.startswith("unlinked:") else s,
                            "name": names.get(s), "linked": not s.startswith("unlinked:"),
                            **{k: p.get(k) for k in ("team", "rank", "rank_short", "rank_name", "placed",
                                                     "elo_win", "elo_loss", "elo_matches")},
                            "elo": p["elo"] if p.get("placed") else None})
        return {"win_prob_a": pr["win_prob_a"], "win_prob_b": pr["win_prob_b"], "players": players}

    def rank_images(self):
        """Rank badge images: the bundled set (web/ranks/1..18.png), replaced by any the server
        owner dropped in RANK_IMAGE_DIR."""
        out = {}
        bundled = os.path.join(WEB_DIR, "ranks")
        if os.path.isdir(bundled):
            for f in os.listdir(bundled):
                stem, ext = os.path.splitext(f)
                if stem.isdigit() and 1 <= int(stem) <= 18 and ext.lower() == ".png":
                    out[int(stem)] = f"/ranks/{f}"
        if os.path.isdir(self.rank_dir):
            for f in os.listdir(self.rank_dir):
                stem, ext = os.path.splitext(f)
                if stem.isdigit() and 1 <= int(stem) <= 18 and ext.lower() in (".png", ".webp", ".svg", ".jpg"):
                    out[int(stem)] = f"/ranks/{f}"
        return out

    def discord_ratings(self, discord_ids):
        links = self.db.discord_links()                     # steamid -> discord_id
        by_discord = {d: s for s, d in links.items()}
        players, _ = self.elo()
        out = {}
        for did in discord_ids:
            sid = by_discord.get(str(did))
            if not sid:
                continue
            info = self.db.player(sid) or {}
            e = players.get(sid, {})
            out[str(did)] = {"steamid": sid, "name": info.get("name") or sid,
                             "elo": e.get("elo", round(elo_mod.START)), "rank_name": e.get("rank_name", "Unranked"),
                             "rank_short": e.get("rank_short", "—"), "placed": e.get("placed", False)}
        return out


# ----------------------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    svc: StatsService = None          # set in main()
    server_version = "cs2stats"

    def log_message(self, fmt, *args):
        log.debug("%s " + fmt, self.address_string(), *args)

    # ---- responses ----
    def _send(self, code, body: bytes, ctype="application/json", cache="no-store"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(_clean(obj), ensure_ascii=False, default=str).encode())

    def _err(self, code, msg):
        self._json({"error": msg}, code)

    def _client_ip(self):
        """The visitor's address. Behind the Cloudflare tunnel every request comes from cloudflared,
        so use the header Cloudflare adds; otherwise the direct connection."""
        for h in ("CF-Connecting-IP", "X-Forwarded-For"):
            v = self.headers.get(h, "").split(",")[0].strip()
            if v:
                return v
        return self.client_address[0]

    def _authed(self):
        key = self.svc.api_key
        given = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        return bool(key) and hmac.compare_digest(given, key)

    def _body(self, limit=10_000):
        n = int(self.headers.get("Content-Length") or 0)
        if n > limit:
            return None
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return None

    # ---- routing ----
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        parts = [p for p in url.path.split("/") if p]
        try:
            if parts[:1] != ["api"]:
                return self._static(url.path)
            route = parts[1:]
            svc = self.svc
            if route == ["health"]:
                return self._json({"ok": True, "matches": svc.db.count_matches()})
            if route == ["summary"]:
                return self._json(svc.summary())
            if route == ["leaderboard"]:
                season = svc.season_param(q.get("season"))
                return self._json({"players": svc.leaderboard(q.get("period"), q.get("map") or None, season),
                                   "season": elo_mod.season_info(season) if season else None,
                                   "min_matches": svc.min_matches})
            if route == ["ranks"]:
                return self._json({"ranks": elo_mod.ranks_table(), "images": svc.rank_images(),
                                   "placement_matches": elo_mod.PLACEMENT_MATCHES})
            if route == ["join-check"]:
                # On the server's home network the public address doesn't work (the router can't loop
                # back), so the join page uses the LAN address. Home = a private address (opened on the
                # LAN directly) or the same public IP as the CS2 server.
                host = q.get("host", "")
                if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", host):
                    return self._err(400, "bad host")
                try:
                    ip = ipaddress.ip_address(self._client_ip())
                except ValueError:
                    return self._json({"home": False})
                home = ip.is_private or ip.is_loopback or str(ip) in resolve(host)
                return self._json({"home": home})
            if route == ["seasons"]:
                return self._json(svc.seasons())
            if route == ["achievements"]:
                c = svc.comm()
                return self._json({"achievements": community.achievement_catalog(c["ach"], c["player_count"])})
            if route == ["superlatives"]:
                return self._json(svc.superlatives(svc.season_param(q.get("season")), q.get("period")))
            if route == ["recap"]:
                return self._json(svc.recap(q.get("since"), q.get("until"), int(q.get("days", 7))))
            if route == ["maps"]:
                return self._json({"maps": svc.db.maps_played()})
            if route == ["players"]:
                return self._json(svc.db.player_names(q.get("q", ""), limit=25))
            if len(route) == 2 and route[0] == "players":
                if not STEAMID.match(route[1]):
                    return self._err(400, "bad steamid")
                data = svc.player(route[1])
                return self._json(data) if data else self._err(404, "no matches for that player")
            if route == ["matches"]:
                limit = max(1, min(int(q.get("limit", 20)), 100))
                offset = max(0, int(q.get("offset", 0)))
                return self._json({"matches": svc.db.recent_matches(limit, offset),
                                   "total": svc.db.count_matches()})
            if len(route) == 2 and route[0] == "matches":
                mid = None if route[1] == "latest" else int(route[1])
                data = svc.match(mid)
                return self._json(data) if data else self._err(404, "no such match")
            # ---- bot-only ----
            if route[:1] in (["announcements"], ["discord"], ["links"], ["kv"], ["events"]) and not self._authed():
                return self._err(401, "missing or wrong STATS_API_KEY")
            if route == ["announcements"]:
                links = svc.db.discord_links()
                out = []
                for mid in svc.db.pending_announcements():
                    data = svc.match(mid)
                    if data:
                        data["discord"] = {p["steamid"]: links[p["steamid"]]
                                           for p in data["players"] if p["steamid"] in links}
                        out.append(data)
                return self._json({"matches": out})
            if route == ["links"]:
                return self._json({"players": svc.db.players_with_links()})
            if len(route) == 2 and route[0] == "kv":
                return self._json({"key": route[1], "value": svc.db.kv_get(route[1])})
            if route == ["events"]:
                return self._json(svc.events_after(int(q.get("after", 0) or 0)))
            if len(route) == 2 and route[0] == "discord":
                return self._json({"steamid": svc.db.steamid_for_discord(route[1])})
            return self._err(404, "not found")
        except (ValueError, TypeError):
            return self._err(400, "bad request")
        except Exception:
            log.exception("GET %s failed", self.path)
            return self._err(500, "server error")

    def do_POST(self):
        parts = [p for p in urlparse(self.path).path.split("/") if p]
        if parts[:1] != ["api"]:
            return self._err(404, "not found")
        if not self._authed():
            return self._err(401, "missing or wrong STATS_API_KEY")
        route, svc = parts[1:], self.svc
        try:
            if len(route) == 3 and route[0] == "announcements" and route[2] == "done":
                svc.db.mark_announced(int(route[1]))
                return self._json({"ok": True})
            if route == ["link"]:
                b = self._body() or {}
                sid, did = str(b.get("steamid", "")), str(b.get("discord_id", ""))
                if not STEAMID.match(sid) or not did.isdigit():
                    return self._err(400, "need discord_id and a SteamID64")
                owner = (svc.db.player(sid) or {}).get("discord_id")
                if owner and owner != did and not b.get("force"):   # don't let someone take over another player
                    return self._json({"error": "already linked", "discord_id": owner}, 409)
                previous = svc.db.steamid_for_discord(did)
                svc.db.link(did, sid)
                known = svc.db.player(sid) or {}
                return self._json({"ok": True, "steamid": sid, "name": known.get("name"),
                                   "replaced_discord_id": owner if owner and owner != did else None,
                                   "previous_steamid": previous if previous and previous != sid else None})
            if route == ["unlink"]:
                b = self._body() or {}
                svc.db.unlink(str(b.get("discord_id", "")))
                return self._json({"ok": True})
            if route == ["rescan"]:
                return self._json({"ok": True, "new": svc.rescan()})
            if route == ["matchzy-event"]:
                b = self._body(limit=2_000_000)
                if isinstance(b, dict):
                    svc.add_event(b)
                return self._json({"ok": True})
            if route == ["predict"]:
                b = self._body() or {}
                return self._json(svc.predict(list(b.get("team_a") or [])[:20], list(b.get("team_b") or [])[:20]))
            if len(route) == 2 and route[0] == "kv":
                b = self._body() or {}
                v = b.get("value")
                svc.db.kv_set(route[1][:64], None if v is None else str(v)[:4000])
                return self._json({"ok": True})
            if route == ["discord-ratings"]:
                b = self._body() or {}
                return self._json({"players": svc.discord_ratings(list(b.get("discord_ids") or [])[:40])})

            return self._err(404, "not found")
        except (ValueError, TypeError) as e:
            return self._err(400, str(e) or "bad request")
        except Exception:
            log.exception("POST %s failed", self.path)
            return self._err(500, "server error")

    def do_DELETE(self):
        parts = [p for p in urlparse(self.path).path.split("/") if p]
        if not self._authed():
            return self._err(401, "missing or wrong STATS_API_KEY")
        if len(parts) == 3 and parts[:2] == ["api", "matches"] and parts[2].isdigit():
            self.svc.db.delete_match(int(parts[2]))
            return self._json({"ok": True})
        return self._err(404, "not found")

    # ---- website (one page; /player/<id> and /match/<id> are client-side routes) ----
    def _static(self, path):
        a = re.fullmatch(r"/ach/([a-z0-9]+)\.svg", path)
        if a:
            body = icons.svg(a.group(1), int(parse_qs(urlparse(self.path).query).get("s", ["64"])[0][:3] or 64),
                             locked="locked" in self.path)
            return self._send(200, body.encode(), "image/svg+xml", cache="public, max-age=86400") if body \
                else self._err(404, "no such achievement")
        m = re.fullmatch(r"/ranks/(\d{1,2}\.(?:png|webp|svg|jpg))", path)
        if m:
            file = os.path.join(self.svc.rank_dir, m.group(1))
            if not os.path.isfile(file):
                file = os.path.join(WEB_DIR, "ranks", m.group(1))       # bundled badge
            if os.path.isfile(file):
                with open(file, "rb") as f:
                    return self._send(200, f.read(), mimetypes.guess_type(file)[0] or "image/png",
                                      cache="public, max-age=3600")
            return self._err(404, "no such image")
        name = path.lstrip("/")
        if name.rstrip("/") == "join":                 # one-click "Join server" page linked from Discord
            name = "join.html"
        file = os.path.normpath(os.path.join(WEB_DIR, name)) if name else ""
        if name and file.startswith(WEB_DIR + os.sep) and os.path.isfile(file):
            ctype = mimetypes.guess_type(file)[0] or "application/octet-stream"
            with open(file, "rb") as f:
                return self._send(200, f.read(), ctype, cache="public, max-age=300")
        with open(os.path.join(WEB_DIR, "index.html"), "rb") as f:
            return self._send(200, f.read(), "text/html; charset=utf-8", cache="no-cache")


def load_dotenv(path=".env"):
    """Minimal .env reader for running outside Docker."""
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.split(" #")[0].strip().strip('"').strip("'"))


def main():
    load_dotenv()
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    svc = StatsService()
    if not svc.api_key:
        log.warning("STATS_API_KEY is not set: the Discord bot can't post scorecards or link accounts")
    Handler.svc = svc
    svc.start_watcher()
    port = env_int("PORT", 8080)
    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    log.info("serving on :%s, watching %s", port, svc.watcher.dir)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
