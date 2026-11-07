# match-bot

Discord bot for our CS2 5v5s: team picking (`/startmatch`), joining the server (`/connect`),
and Leetify-style stats with a leaderboard website.

## How it fits together

| Container | What it does |
|---|---|
| `matchbot` (Node, this folder) | The Discord bot. Lobby + captains draft, connect buttons, and all the stats commands. Posts a scorecard to the stats channel after every match. |
| `stats` (Python, `stats/`) | No Discord login. Watches MatchZy's demo folder (read-only), parses each finished demo, stores stats in SQLite (`stats-data/stats.db`), and serves the leaderboard website + a small API on port 8080. |

```
MatchZy demo (.dem) ─► stats: parse ─► SQLite ─► website (/, /player/<steamid>, /match/<id>, /community, /achievements, /seasons)
                                          └────► matchbot: /leaderboard, /stats, auto scorecards
```

**Setup on the server:** see [`docs/cs2-stats-setup-guide.pdf`](docs/cs2-stats-setup-guide.pdf).
Short version: fill in the stats section of `.env` (see `.env.example`), then `docker compose up -d --build`.

## Commands

| Command | |
|---|---|
| `/startmatch`, `/connect` | Lobby (starter can **Add Everyone in Voice**, **Choose Players** or run an optional **Map Vote**), captains, draft (or **Randomize Teams**: 2 random captains, Elo-balanced), win prediction with each player's Elo at stake, move to voice, **Back to Lobby**; how to join the server |
| `/matchnight when [players] [note]` | Signup with In / Maybe / Can't. Pings the In list when there's enough, reminds them 30 min before, opens a lobby with them at start time |
| `/recap [days]` · `/status` | This week's highlights · is the server up, what map, who's on |
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

## Community features

- **Win prediction**: posted when teams are set (draft or Randomize): each team's chance from everyone's Elo,
  and how much Elo each player can win or lose (a range, because Rating 3.0 scales it ±30%). Match pages and
  scorecards show the pre-match odds and flag upsets.
- **Achievements**: 40 badges (original artwork, `stats/cs2stats/icons.py`) computed from the match history:
  feats like Ace or a 1v4 clutch, milestones, rank tiers, season podiums, and a small hall of shame.
  New ones are posted with the scorecard; profiles show the full grid with rarity.
- **Who killed who**: every demo records kills, headshots and opening duels between each pair of players.
  Profiles get head-to-head and teammate tables, nemesis / favorite victim, and opening duels split by T/CT.
  Demos read by an older version are re-read once in the background to fill this in.
- **Duos & rivalries**: best/worst/most-played duo, biggest rivalry, most one-sided matchup (`/community`).
- **Weekly recap**: posted every Monday at noon (`RECAP_DAY`, `RECAP_HOUR`), also `/recap`.
- **Seasons**: every 3 months. Elo moves halfway back to 1500, ranks show again after 2 matches; the final
  standings are posted, and the top 3 earn achievements. Season filter on the leaderboard.
- **Live server status**: one message in `STATUS_CHANNEL_ID` kept up to date (map, players, Join button), plus
  the bot's status line. Uses Steam's standard server query; nothing is sent to the server's console.
- **Back to lobby**: when the scorecard posts, everyone in the team channels goes back to `LOBBY_VOICE_CHANNEL_ID`.
- **Map vote** (optional): the match starter picks maps we've played and/or types any map name (typed maps
  aren't saved); lobby players vote with buttons, the starter ends it.

## Development

```bash
npm ci                                   # bot
python stats/tools/render_icons.py       # after editing badge artwork: re-render the PNGs for Discord
cd stats && pip install -r requirements.txt pytest && python -m pytest -q tests
python -m cs2stats.server                # website on :8080 (reads ../.env if run from the repo root)
python -m cs2stats.cli show-demo some.dem   # print one demo's stat table, no database
```
