// Things that happen on a clock: the weekly recap, the end-of-season post and match night
// reminders. What was last posted is kept in the stats service, so restarts don't double-post.
const stats = require("./stats");
const { recapEmbed, seasonMessage } = require("./communityEmbeds");
const matchNight = require("./matchNight");
const { parts } = require("./time");

const DAYS = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"];
const RECAP_DAY = (process.env.RECAP_DAY || "mon").trim().toLowerCase();       // "off" to disable
const RECAP_HOUR = parseInt(process.env.RECAP_HOUR || "12", 10);

async function channel(client, id) {
    return id ? client.channels.fetch(id).catch(() => null) : null;
}

async function weeklyRecap(client) {
    const day = DAYS.indexOf(RECAP_DAY.slice(0, 3));
    const ch = await channel(client, process.env.RECAP_CHANNEL_ID || process.env.STATS_CHANNEL_ID);
    if (day < 0 || !ch) return;
    const now = parts();
    if (now.weekday !== day || now.hour < RECAP_HOUR) return;
    const stamp = `${now.year}-${now.month}-${now.day}`;
    if ((await stats.kvGet("recap_last")) === stamp) return;
    const rc = await stats.recap(7);
    await stats.kvSet("recap_last", stamp);
    if (!rc.matches) return console.log("[recap] no matches this week, skipped");
    await ch.send({ embeds: [recapEmbed(rc)] });
    console.log("[recap] posted");
}

async function seasonEnd(client) {
    const ch = await channel(client, process.env.RECAP_CHANNEL_ID || process.env.STATS_CHANNEL_ID);
    if (!ch) return;
    const { seasons } = await stats.seasons();
    const done = seasons.filter((s) => s.finished).sort((a, b) => b.season - a.season)[0];
    if (!done) return;
    const posted = parseInt((await stats.kvGet("season_posted")) || "0", 10);
    if (posted >= done.season) return;
    await stats.kvSet("season_posted", String(done.season));
    await ch.send(await seasonMessage(done));
    console.log(`[season] posted ${done.name} results`);
}

function startSchedules(client) {
    if (!process.env.STATS_API_KEY) return;
    const hourly = async () => {
        for (const job of [weeklyRecap, seasonEnd]) {
            try { await job(client); } catch (err) { console.error(`[schedule] ${job.name}:`, err.message); }
        }
    };
    const minutely = () => matchNight.tick(client).catch((err) => console.error("[match night]", err.message));
    setInterval(hourly, 15 * 60 * 1000);
    setInterval(minutely, 60 * 1000);
    setTimeout(hourly, 20 * 1000);
    setTimeout(minutely, 5 * 1000);
}

module.exports = { startSchedules };
