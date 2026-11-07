const { SlashCommandBuilder } = require("discord.js");
const stats = require("../lib/stats");

// Accepts a SteamID64, a /profiles/7656... URL, [U:1:123] / U:1:123, or (with STEAM_API_KEY) a custom /id/ URL
async function parseSteam(text) {
    text = text.trim();
    let m = text.match(/(7656119\d{10})/);
    if (m) return m[1];
    m = text.match(/\[?U:1:(\d+)\]?/i);
    if (m) return (76561197960265728n + BigInt(m[1])).toString();
    m = text.match(/steamcommunity\.com\/id\/([^/?#\s]+)/i) || text.match(/^([A-Za-z0-9_-]{2,32})$/);
    if (m && process.env.STEAM_API_KEY) {
        const url = `https://api.steampowered.com/ISteamUser/ResolveVanityURL/v1/?key=${process.env.STEAM_API_KEY}&vanityurl=${encodeURIComponent(m[1])}`;
        const res = await fetch(url, { signal: AbortSignal.timeout(8000) }).then((r) => r.json()).catch(() => null);
        if (res?.response?.success === 1) return res.response.steamid;
    }
    return null;
}

module.exports = {
    data: new SlashCommandBuilder()
        .setName("link")
        .setDescription("Link your Discord account to your Steam account for CS2 stats")
        .addStringOption((o) => o.setName("steam").setDescription("Steam profile URL or SteamID64").setRequired(true)),

    async execute(interaction) {
        await interaction.deferReply({ ephemeral: true });
        const steamid = await parseSteam(interaction.options.getString("steam"));
        if (!steamid)
            return interaction.editReply(
                "I couldn't read a Steam ID from that. Paste a profile link with the number in it " +
                "(steamcommunity.com/profiles/7656…) or your SteamID64." +
                (process.env.STEAM_API_KEY ? "" : " Custom /id/ links need a Steam API key on the bot."));
        const res = await stats.link(interaction.user.id, steamid);
        await interaction.editReply(`Linked to \`${steamid}\`` +
            (res.name ? ` (**${res.name}** in our matches). \`/stats\` now shows you by default.` : ". No matches with that account yet, but they'll count once you play."));
    },
    parseSteam
};
