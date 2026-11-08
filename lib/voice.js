// Moving people back to the lobby voice channel after a match.
const config = require("../config.json");

// The lobby channel: LOBBY_VOICE_CHANNEL_ID, else the channel the match starter was in.
function lobbyChannelId(fallback) {
    return (process.env.LOBBY_VOICE_CHANNEL_ID || config.lobbyVoiceChannelId || "").trim() || fallback || null;
}

// Everyone in the two team channels goes to the lobby. Returns how many were moved.
async function moveTeamsToLobby(guild, lobbyId) {
    if (!guild || !lobbyId) return 0;
    let moved = 0;
    for (const id of [config.team1VoiceChannelId, config.team2VoiceChannelId]) {
        const ch = guild.channels.cache.get(id) || await guild.channels.fetch(id).catch(() => null);
        if (!ch || !ch.members || ch.id === lobbyId) continue;
        for (const m of ch.members.values()) {
            try { await m.voice.setChannel(lobbyId); moved++; } catch (_) {}
        }
    }
    return moved;
}

module.exports = { lobbyChannelId, moveTeamsToLobby };
