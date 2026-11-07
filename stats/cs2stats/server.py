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
  GET  /api/matchconfig/<matchid>.json     match setup for MatchZy (matchzy_loadmatch_url)

Bot-only endpoints — need "Authorization: Bearer $STATS_API_KEY":
  GET  /api/announcements                 matches waiting to be posted to Discord
  POST /api/announcements/<id>/done
  GET  /api/discord/<discord_id>          -> linked steamid
  POST /api/link     {"discord_id", "steamid"}
  POST /api/unlink   {"discord_id"}
  POST /api/rescan
  POST /api/discord-ratings {"discord_ids": [...]}  -> Elo for linked members (team balancing)
  POST /api/matchconfig {"team1": {"name", "players": {steamid: name}}, "team2": ..., "map"}
  DELETE /api/matches/<id>
"""
from __future__ import annotations

import asyncio
import hmac
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

from . import elo as elo_mod
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
        self._elo = (None, {}, {})
        self._elo_lock = threading.Lock()

    # ---- Elo: replayed from the match history, cached until matches change ----
    def elo(self):
        v = self.db.version()
        with self._elo_lock:
            if self._elo[0] != v:
                players, per_match = elo_mod.compute(self.db.elo_rows())
                self._elo = (v, players, per_match)
            return self._elo[1], self._elo[2]

    def _with_elo(self, rows, match_id=None):
        players, per_match = self.elo()
        for r in rows:
            r.update(players.get(r["steamid"], {}))
            mid = match_id or r.get("match_id")
            if mid is not None:
                r.update(per_match.get((mid, r["steamid"]), {}))
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
    def leaderboard(self, period=None, map_name=None):
        rows = self._with_elo(with_categories(self.db.aggregates(since=_since(period), map_name=map_name)))
        rows.sort(key=lambda r: (r["rating"] is None, -(r["rating"] or 0)))   # unrated last
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
        agg["position"] = (1 + sum(1 for r in rows if r["rating"] is not None and r["rating"] > agg["rating"]
                                   and r["matches"] >= self.min_matches)) if agg["rating"] is not None else None
        history = self._with_elo(self.db.player_history(steamid, limit=500))
        by_map = []
        for m in sorted({h["map"] for h in history}):
            r = next((x for x in self.db.aggregates(map_name=m) if x["steamid"] == steamid), None)
            if r:
                by_map.append({"map": m, **{k: r[k] for k in (
                    "matches", "wins", "losses", "rating", "kd", "adr", "kast", "kills", "deaths")}})
        info = self.db.player(steamid) or {}
        return {"player": agg, "history": history, "maps": by_map,
                "linked": bool(info.get("discord_id")), "group_size": len(rows)}

    def match(self, match_id):
        m, ps = self.db.match(match_id)
        if not m:
            return None
        return {"match": m, "players": self._with_elo(ps, match_id=m["id"]), "highlights": highlights(ps)}

    def rank_images(self):
        """Custom badge images the server owner dropped in RANK_IMAGE_DIR (1.png ... 18.png)."""
        out = {}
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

    def make_matchzy_config(self, body):
        """Match setup for MatchZy: our two teams, one map, knife round for sides."""
        teams = {}
        for k in ("team1", "team2"):
            t = body.get(k) or {}
            players = {str(s): str(n)[:32] for s, n in (t.get("players") or {}).items() if STEAMID.match(str(s))}
            if not players:
                raise ValueError(f"{k} has no players with a SteamID64")
            teams[k] = {"name": re.sub(r"[^\w .-]", "", str(t.get("name") or k))[:32] or k, "players": players}
        mp = str(body.get("map") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_\-]{2,64}", mp):
            raise ValueError("bad map name")
        size = max(len(teams["team1"]["players"]), len(teams["team2"]["players"]))
        cfg = {"matchid": 0, "team1": teams["team1"], "team2": teams["team2"],
               "num_maps": 1, "maplist": [mp], "map_sides": ["knife"], "skip_veto": True,
               "clinch_series": True, "players_per_team": size,
               "min_players_to_ready": len(teams["team1"]["players"]) + len(teams["team2"]["players"]),
               "cvars": {}}
        mid = self.db.save_matchzy_config(cfg)
        return {"matchid": mid, "path": f"/api/matchconfig/{mid}.json", "config": cfg}


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

    def _authed(self):
        key = self.svc.api_key
        given = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        return bool(key) and hmac.compare_digest(given, key)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 10_000:
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
                return self._json({"players": svc.leaderboard(q.get("period"), q.get("map") or None),
                                   "min_matches": svc.min_matches})
            if route == ["ranks"]:
                return self._json({"ranks": elo_mod.ranks_table(), "images": svc.rank_images(),
                                   "placement_matches": elo_mod.PLACEMENT_MATCHES})
            if len(route) == 2 and route[0] == "matchconfig" and route[1].endswith(".json"):
                cfg = svc.db.matchzy_config(route[1][:-5])
                return self._json(cfg) if cfg else self._err(404, "no such match setup")
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
            if route[:1] in (["announcements"], ["discord"]) and not self._authed():
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
                svc.db.link(did, sid)
                known = svc.db.player(sid) or {}
                return self._json({"ok": True, "steamid": sid, "name": known.get("name")})
            if route == ["unlink"]:
                b = self._body() or {}
                svc.db.unlink(str(b.get("discord_id", "")))
                return self._json({"ok": True})
            if route == ["rescan"]:
                return self._json({"ok": True, "new": svc.rescan()})
            if route == ["discord-ratings"]:
                b = self._body() or {}
                return self._json({"players": svc.discord_ratings(list(b.get("discord_ids") or [])[:40])})
            if route == ["matchconfig"]:
                b = self._body()
                if b is None:
                    return self._err(400, "bad JSON")
                return self._json(svc.make_matchzy_config(b))
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
        m = re.fullmatch(r"/ranks/(\d{1,2}\.(?:png|webp|svg|jpg))", path)
        if m:
            file = os.path.join(self.svc.rank_dir, m.group(1))
            if os.path.isfile(file):
                with open(file, "rb") as f:
                    return self._send(200, f.read(), mimetypes.guess_type(file)[0] or "image/png",
                                      cache="public, max-age=3600")
            return self._err(404, "no such image")
        name = path.lstrip("/")
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
