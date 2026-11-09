// Posts a scorecard to the stats channel as soon as the stats service has
// processed a new MatchZy demo. The service keeps the queue in its database, so
// nothing is lost or posted twice if either container restarts.
const stats = require("./stats");
const { matchMessage } = require("./statsEmbeds");
const { achievementMessages } = require("./communityEmbeds");
const { lobbyChannelId, moveTeamsToLobby } = require("./voice");

// When a match ends, move everyone in the team channels back to the lobby channel
// (LOBBY_VOICE_CHANNEL_ID). AUTO_LOBBY_RETURN=false turns it off.
// Fast path: MatchZy tells the stats service the moment a map ends (matchzy_remote_log_url), and
// the bot checks for that every few seconds. Slow path (MatchZy not set up): when the scorecard posts.
const AUTO_RETURN = !/^(0|false|no)$/i.test(process.env.AUTO_LOBBY_RETURN || "true");
const RETURN_DELAY_MS = (parseInt(process.env.LOBBY_RETURN_DELAY_SECONDS || "5", 10) || 0) * 1000;
// Achievement unlocks stay on the website; set ANNOUNCE_ACHIEVEMENTS=true to also post them in Discord
const ANNOUNCE_ACHIEVEMENTS = /^(1|true|yes)$/i.test(process.env.ANNOUNCE_ACHIEVEMENTS || "false");
let lastReturn = 0;

async function returnToLobby(client, guild, why) {
    if (!AUTO_RETURN || !guild) return;
    if (Date.now() - lastReturn < 10 * 60 * 1000) return;     // already done for this match
    const recent = [...client.matches.values()].reverse().find((m) => m.lobbyVoiceId);
    const lobby = lobbyChannelId(recent?.lobbyVoiceId);
    if (!lobby) return;
    lastReturn = Date.now();
    const n = await moveTeamsToLobby(guild, lobby);
    if (n) console.log(`[lobby] ${why}: moved ${n} players back to the lobby channel`);
}

// Polls the stats service for MatchZy's "map_result" / "series_end" events
function startEndWatcher(client, guildOf) {
    let after = null, busy = false;
    const tick = async () => {
        if (busy) return;
        busy = true;
        try {
            const { events, last } = await stats.events(after ?? 0);
            if (after === null) { after = last; return; }       // ignore events from before the bot started
            after = last;
            if (events.some((e) => e.event === "map_result" || e.event === "series_end")) {
                setTimeout(() => returnToLobby(client, guildOf(), "match ended")
                    .catch((err) => console.error("[lobby]", err.message)), RETURN_DELAY_MS);
            }
            if (events.some((e) => e.event === "going_live")) lastReturn = 0;   // new match: allow a return again
        } catch (_) {
            // stats service down or an older version without /api/events: the scorecard path still works
        } finally {
            busy = false;
        }
    };
    setInterval(tick, 3000);
    tick();
}

const POLL_MS = (parseInt(process.env.MATCH_FEED_SECONDS || "20", 10) || 20) * 1000;

function startMatchFeed(client) {
    const channelId = process.env.STATS_CHANNEL_ID;
    if (process.env.STATS_API_KEY) {
        const cfg = require("../config.json");
        startEndWatcher(client, () => client.channels.cache.get(cfg.team1VoiceChannelId)?.guild || client.guilds.cache.first());
    }
    if (!channelId) {
        console.log("[match feed] STATS_CHANNEL_ID not set: match scorecards won't be posted");
        return;
    }
    if (!process.env.STATS_API_KEY) {
        console.log("[match feed] STATS_API_KEY not set: match scorecards won't be posted");
        return;
    }

    let busy = false, lastError = "";
    const tick = async () => {
        if (busy) return;
        busy = true;
        try {
            const { matches } = await stats.announcements();
            if (matches.length) {
                const channel = await client.channels.fetch(channelId);
                for (const data of matches) {
                    const discord = data.discord || {};
                    const mvp = data.highlights?.find((h) => h.key === "mvp");
                    const mvpUser = mvp && discord[mvp.steamid];
                    await channel.send({
                        content: `GG! **${data.match.map.replace(/^de_/, "")}** just finished.` +
                            (mvpUser ? ` MVP: <@${mvpUser}> ⭐` : mvp ? ` MVP: **${mvp.name}** ⭐` : ""),
                        ...matchMessage(data, discord),
                        allowedMentions: { users: mvpUser ? [mvpUser] : [] }   // only ping the MVP
                    });
                    await stats.markAnnounced(data.match.id);
                    console.log(`[match feed] posted match #${data.match.id}`);
                    if (ANNOUNCE_ACHIEVEMENTS) {
                        for (const msg of await achievementMessages(data.achievements, discord)) {
                            try { await channel.send(msg); } catch (err) { console.error("[match feed] achievements:", err.message); }
                        }
                    }
                    await returnToLobby(client, channel.guild, "scorecard posted").catch((err) => console.error("[lobby]", err.message));
                }
            }
            lastError = "";
        } catch (err) {
            if (err.message !== lastError) console.error("[match feed]", err.message);  // don't spam the log
            lastError = err.message;
        } finally {
            busy = false;
        }
    };
    setInterval(tick, POLL_MS);
    tick();
}

module.exports = { startMatchFeed };
