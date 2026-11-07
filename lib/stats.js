// Client for the stats service (stats/ folder, runs as its own container).
// The service parses MatchZy demos and keeps the database; this bot only reads
// from it and posts to Discord, so there is still just one Discord bot.

const BASE = (process.env.STATS_API_URL || "http://stats:8080").replace(/\/+$/, "");
const PUBLIC_URL = (process.env.STATS_PUBLIC_URL || "").replace(/\/+$/, "");
const API_KEY = process.env.STATS_API_KEY || "";

class StatsError extends Error {
    constructor(message, status) {
        super(message);
        this.name = "StatsError";
        this.status = status;
    }
}

async function call(path, { method = "GET", body, auth = false } = {}) {
    const headers = {};
    if (auth) headers.Authorization = `Bearer ${API_KEY}`;
    if (body) headers["Content-Type"] = "application/json";
    let res;
    try {
        res = await fetch(`${BASE}/api/${path}`, {
            method,
            headers,
            body: body ? JSON.stringify(body) : undefined,
            signal: AbortSignal.timeout(15000)
        });
    } catch (err) {
        throw new StatsError("The stats service isn't reachable right now.", 0);
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new StatsError(data.error || `Stats service error (${res.status})`, res.status);
    return data;
}

// Link to a page on the leaderboard website, or null if STATS_PUBLIC_URL isn't set
function pageUrl(path = "/") {
    if (!/^https?:\/\//.test(PUBLIC_URL)) return null;
    return PUBLIC_URL + path;
}

module.exports = {
    StatsError,
    pageUrl,
    summary: () => call("summary"),
    leaderboard: (period = "all") => call(`leaderboard?period=${encodeURIComponent(period)}`),
    searchPlayers: (q) => call(`players?q=${encodeURIComponent(q)}`),
    player: (steamid) => call(`players/${steamid}`),
    match: (id) => call(`matches/${id ?? "latest"}`),
    matches: (limit = 10) => call(`matches?limit=${limit}`),
    steamidForDiscord: async (discordId) => (await call(`discord/${discordId}`, { auth: true })).steamid,
    link: (discordId, steamid) => call("link", { method: "POST", body: { discord_id: discordId, steamid }, auth: true }),
    unlink: (discordId) => call("unlink", { method: "POST", body: { discord_id: discordId }, auth: true }),
    announcements: () => call("announcements", { auth: true }),
    markAnnounced: (id) => call(`announcements/${id}/done`, { method: "POST", auth: true }),
    deleteMatch: (id) => call(`matches/${id}`, { method: "DELETE", auth: true }),
    rescan: () => call("rescan", { method: "POST", auth: true }),
    discordRatings: (ids) => call("discord-ratings", { method: "POST", body: { discord_ids: ids }, auth: true }),
    makeMatchConfig: (body) => call("matchconfig", { method: "POST", body, auth: true })
};
