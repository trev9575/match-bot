import json, os, sys, threading, time, urllib.request, urllib.error
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


def test_elo_fields_and_balancing_lookup(tmp_path, monkeypatch):
    svc, base = start(tmp_path, monkeypatch)
    fake_match(svc.db, announce=False)
    lb = call(base + "/api/leaderboard")[1]["players"]
    p0 = next(p for p in lb if p["steamid"] == SIDS[0])
    assert p0["elo"] is None and p0["elo_sort"] > 1500                  # hidden during placement, still sortable
    assert p0["rank"] == 0 and "Unranked" in p0["rank_name"]           # 1 of 5 placements
    assert "rating2" not in p0
    m = call(base + "/api/matches/1")[1]
    assert all("elo_delta" in p for p in m["players"])

    svc.db.link("42", SIDS[0])
    code, r = call(base + "/api/discord-ratings", "POST", {"discord_ids": ["42", "99"]}, key="secret")
    assert code == 200 and list(r["players"]) == ["42"] and r["players"]["42"]["steamid"] == SIDS[0]


def test_link_guard_and_join_page(tmp_path, monkeypatch):
    svc, base = start(tmp_path, monkeypatch)
    fake_match(svc.db, announce=False)
    assert call(base + "/api/link", "POST", {"discord_id": "1", "steamid": SIDS[0]}, key="secret")[0] == 200
    assert call(base + "/api/link", "POST", {"discord_id": "1", "steamid": SIDS[0]}, key="secret")[0] == 200   # same person again
    code, r = call(base + "/api/link", "POST", {"discord_id": "2", "steamid": SIDS[0]}, key="secret")
    assert code == 409 and r["discord_id"] == "1"                     # someone else can't take it over
    with urllib.request.urlopen(base + "/join") as resp:
        assert b"steam://connect/" in resp.read()


def test_admin_force_link_and_links_list(tmp_path, monkeypatch):
    svc, base = start(tmp_path, monkeypatch)
    fake_match(svc.db, announce=False)
    call(base + "/api/link", "POST", {"discord_id": "1", "steamid": SIDS[0]}, key="secret")
    code, r = call(base + "/api/link", "POST", {"discord_id": "2", "steamid": SIDS[0], "force": True}, key="secret")
    assert code == 200 and r["replaced_discord_id"] == "1"
    assert call(base + "/api/links")[0] == 401
    code, r = call(base + "/api/links", key="secret")
    linked = {p["steamid"]: p["discord_id"] for p in r["players"]}
    assert len(r["players"]) == 10 and linked[SIDS[0]] == "2" and linked[SIDS[1]] is None


def test_join_check_home_network(tmp_path, monkeypatch):
    svc, base = start(tmp_path, monkeypatch)
    # straight from the LAN/localhost: home
    assert call(base + "/api/join-check?host=cs2.example.com")[1] == {"home": True}
    def via_cloudflare(ip):
        req = urllib.request.Request(base + "/api/join-check?host=cs2.example.com", headers={"CF-Connecting-IP": ip})
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())["home"]
    server._dns_cache["cs2.example.com"] = (time.time(), {"75.117.140.191"})
    assert via_cloudflare("75.117.140.191") is True          # same public IP as the game server
    assert via_cloudflare("8.8.8.8") is False        # someone elsewhere
    assert call(base + "/api/join-check?host=bad;host")[0] == 400


def test_match_without_rating3_data_still_loads(tmp_path, monkeypatch):
    svc, base = start(tmp_path, monkeypatch)
    mid = fake_match(svc.db, announce=False)
    with svc.db.conn() as c:                      # simulate an old log import: no Rating 3.0 inputs
        c.execute("UPDATE player_match SET e_kills=0, e_deaths=0, swing=0 WHERE match_id=?", (mid,))
    code, m = call(base + f"/api/matches/{mid}")
    assert code == 200 and all(p["rating"] is None for p in m["players"]) and m["highlights"][0]["key"] == "mvp"
    assert call(base + f"/api/players/{SIDS[0]}")[0] == 200
