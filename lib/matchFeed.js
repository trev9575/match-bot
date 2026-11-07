// Posts a scorecard to the stats channel as soon as the stats service has
// processed a new MatchZy demo. The service keeps the queue in its database, so
// nothing is lost or posted twice if either container restarts.
const stats = require("./stats");
const { matchMessage } = require("./statsEmbeds");

const POLL_MS = (parseInt(process.env.MATCH_FEED_SECONDS || "20", 10) || 20) * 1000;

function startMatchFeed(client) {
    const channelId = process.env.STATS_CHANNEL_ID;
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
