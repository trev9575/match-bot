import json, os, sys, threading, urllib.request, urllib.error
from http.server import ThreadingHTTPServer
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cs2stats import server
from cs2stats.model import MatchData, Round, Kill, T, CT
from cs2stats.stats import compute

SIDS = [str(76561198000000000 + i) for i in range(10)]


def fake_match(db, announce):
    ro = {s: (T if i < 5 else CT) for i, s in enumerate(SIDS)}
    rounds = [Round(n, winner=T, start_tick=n * 1000, roster=dict(ro),
                    kills=[Kill(n * 1000 + j, SIDS[j], SIDS[j + 5], weapon="ak47") for j in range(5)])
              for n in range(1, 14)]
    P, s = compute(MatchData("de_mirage", "x.dem", rounds, {x: f"p{i}" for i, x in enumerate(SIDS)}))
    return db.save_match(f"m{announce}.dem", s, P, played_at="2026-11-05T20:00:00", announce=announce)


def start(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "s.db"))
    monkeypatch.setenv("BACKFILL_DB", str(tmp_path / "none.db"))
    monkeypatch.setenv("STATS_API_KEY", "secret")
    svc = server.StatsService()
    server.Handler.svc = svc
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return svc, f"http://127.0.0.1:{httpd.server_port}"


def call(url, method="GET", body=None, key=None):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body else None)
    if key:
        req.add_header("Authorization", "Bearer " + key)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_api_flow(tmp_path, monkeypatch):
    svc, base = start(tmp_path, monkeypatch)
    fake_match(svc.db, announce=False)
    mid = fake_match(svc.db, announce=True)

    code, s = call(base + "/api/summary")
    assert code == 200 and s["matches"] == 2 and s["players"] == 10
    code, m = call(base + f"/api/matches/{mid}")
    assert m["match"]["round_log"] == "a" * 13 and m["highlights"][0]["key"] == "mvp"
    code, p = call(base + f"/api/players/{SIDS[0]}")
    assert p["player"]["matches"] == 2 and len(p["history"]) == 2

    # bot-only endpoints need the key
    assert call(base + "/api/announcements")[0] == 401
    code, a = call(base + "/api/announcements", key="secret")
    assert [x["match"]["id"] for x in a["matches"]] == [mid]
    assert call(base + "/api/link", "POST", {"discord_id": "42", "steamid": SIDS[0]}, key="wrong")[0] == 401
    code, r = call(base + "/api/link", "POST", {"discord_id": "42", "steamid": SIDS[0]}, key="secret")
    assert code == 200 and r["name"] == "p0"
    assert call(base + "/api/discord/42", key="secret")[1]["steamid"] == SIDS[0]
    code, a = call(base + "/api/announcements", key="secret")
    assert a["matches"][0]["discord"] == {SIDS[0]: "42"}
    call(base + f"/api/announcements/{mid}/done", "POST", key="secret")
    assert call(base + "/api/announcements", key="secret")[1]["matches"] == []

    # website: any non-API path serves the page
    with urllib.request.urlopen(base + "/player/" + SIDS[0]) as r:
        assert b"CS2 5v5 Leaderboard" in r.read()
    assert call(base + "/api/players/notanid")[0] == 400


def test_elo_matchconfig_and_balancing_lookup(tmp_path, monkeypatch):
    svc, base = start(tmp_path, monkeypatch)
    fake_match(svc.db, announce=False)
    lb = call(base + "/api/leaderboard")[1]["players"]
    p0 = next(p for p in lb if p["steamid"] == SIDS[0])
    assert p0["elo"] > 1500 and p0["rank"] == 0 and "Unranked" in p0["rank_name"]   # 1 of 5 placements
    assert "rating2" not in p0
    m = call(base + "/api/matches/1")[1]
    assert all("elo_delta" in p for p in m["players"])

    svc.db.link("42", SIDS[0])
    code, r = call(base + "/api/discord-ratings", "POST", {"discord_ids": ["42", "99"]}, key="secret")
    assert code == 200 and list(r["players"]) == ["42"] and r["players"]["42"]["steamid"] == SIDS[0]

    body = {"team1": {"name": "Sam", "players": {SIDS[0]: "p0", SIDS[1]: "p1"}},
            "team2": {"name": "Trev", "players": {SIDS[5]: "p5", SIDS[6]: "p6"}}, "map": "de_mirage"}
    assert call(base + "/api/matchconfig", "POST", body)[0] == 401
    code, made = call(base + "/api/matchconfig", "POST", body, key="secret")
    assert code == 200 and made["matchid"] > 1000000
    code, cfg = call(base + made["path"])                       # what MatchZy downloads
    assert cfg["maplist"] == ["de_mirage"] and cfg["map_sides"] == ["knife"]
    assert cfg["team1"]["name"] == "Sam" and cfg["players_per_team"] == 2
    assert call(base + "/api/matchconfig", "POST", {**body, "map": "x; quit"}, key="secret")[0] == 400

    # a demo of that match picks up the captain team names by roster
    from cs2stats.ingest import config_team_names
    from cs2stats.stats import PlayerMatch
    P = {s: PlayerMatch(s, team="B" if s in (SIDS[0], SIDS[1]) else "A") for s in SIDS[:2] + SIDS[5:7]}
    assert config_team_names(cfg, P) == ("Trev", "Sam")
