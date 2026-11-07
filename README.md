# match-bot

Discord bot for our CS2 5v5s: team picking (`/startmatch`), joining the server (`/connect`),
and Leetify-style stats with a leaderboard website.

## How it fits together

| Container | What it does |
|---|---|
| `matchbot` (Node, this folder) | The Discord bot. Lobby + captains draft, connect buttons, and all the stats commands. Posts a scorecard to the stats channel after every match. |
| `stats` (Python, `stats/`) | No Discord login. Watches MatchZy's demo folder (read-only), parses each finished demo, stores stats in SQLite (`stats-data/stats.db`), and serves the leaderboard website + a small API on port 8080. |

```
MatchZy demo (.dem) ─► stats: parse ─► SQLite ─► website (/, /player/<steamid>, /match/<id>)
                                          └────► matchbot: /leaderboard, /stats, auto scorecards
```

**Setup on the server:** see [`docs/cs2-stats-setup-guide.pdf`](docs/cs2-stats-setup-guide.pdf).
Short version: fill in the stats section of `.env` (see `.env.example`), then `docker compose up -d --build`.

## Commands

| Command | |
|---|---|
| `/startmatch`, `/connect` | Lobby (starter can **Add Everyone in Voice** or **Choose Players**), captains, draft (or **Randomize Teams**: 2 random captains, Elo-balanced), move to voice; how to join the server |
| `/leaderboard [stat] [period] [min_matches]` | 10 players per page with Prev/Next, plus a button to the full site |
| `/stats [player] [member]` | Profile: rating, category percentiles, duels, utility, recent matches |
| `/match [id]` · `/matches` | Scorecard for a match · the last 10 matches |
| `/link [player] [steam]` · `/unlink` | Pick your in-game name (or paste a Steam link) to connect your Discord; MVP gets tagged in scorecards |
| `/connect` | One **Join Server** button → the stats site's `/join` page (starts CS2 if needed, then connects) |
| `/links [show]` · `/link member:@x` · `/unlink member:@x` | Admin (Manage Server): see who's linked, and link/unlink anyone |
| `/deletematch <id>` · `/rescan` | Admin (Manage Server): remove a bad game · check for demos now |

## Stats

- **Rating 3.0**: approximation of HLTV Rating 3.0 (Swing 33%, Kills 25%, Survival 14%, KAST 12%,
  Damage 11%, Multi-kill 5%), eco-adjusted. 1.00 = the average player in our games.
- **Rank & Elo**: CS:GO-style. Glicko-2 on wins/losses (what Valve said CS:GO used), replayed from the match
  history, with each player's match Rating 3.0 scaling their change by up to ±30%; 1500 start = Gold Nova III,
  18 ranks, rank and Elo hidden until 5 placement matches, rank hidden after 30 days idle. Leaderboards rank by Elo.
  Our own rank bands and badges; drop `1.png`…`18.png` in the stats data folder's `ranks/` to use your own images.
- **Playstyles chart**: each player placed around a circle of the five profile categories; middle = most
  typical profile, edge = most extreme.
- ADR, KAST, HS%, opening duels, trades, clutches, multi-kills/aces, utility damage, flashes (incl. teammates flashed).
- Category scores (Firepower, Entrying, Trading, Clutching, Utility): 0–100 percentiles within the group.
- Only finished competitive maps count: warmup, knife rounds, retakes, wingman, DM, abandoned games and AFK players are filtered out.
- Steam IDs are stored for every player; `/link` maps Discord users to them.

## Development

```bash
npm ci                                   # bot
cd stats && pip install -r requirements.txt pytest && python -m pytest -q tests
python -m cs2stats.server                # website on :8080 (reads ../.env if run from the repo root)
python -m cs2stats.cli show-demo some.dem   # print one demo's stat table, no database
```
