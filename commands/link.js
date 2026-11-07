const { SlashCommandBuilder, PermissionFlagsBits } = require("discord.js");
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
        .setDescription("Link your Discord account to your CS2 player for stats and ranks")
        .addStringOption((o) => o.setName("player").setDescription("Pick your in-game name from players in our matches").setAutocomplete(true))
        .addStringOption((o) => o.setName("steam").setDescription("Or: your Steam profile URL or SteamID64 (if you haven't played yet)"))
        .addUserOption((o) => o.setName("member").setDescription("(admins) Link someone else instead of yourself")),

    // Names of everyone who has played a match; the value sent back is their SteamID64
    async autocomplete(interaction) {
        const list = await stats.searchPlayers(interaction.options.getFocused()).catch(() => []);
        await interaction.respond(list.slice(0, 25).map((p) => ({ name: (p.name || p.steamid).slice(0, 100), value: p.steamid })));
    },

    async execute(interaction) {
        await interaction.deferReply({ ephemeral: true });
        const member = interaction.options.getUser("member");
        const forOther = member && member.id !== interaction.user.id;
        const isAdmin = interaction.memberPermissions?.has(PermissionFlagsBits.ManageGuild);
        if (forOther && !isAdmin)
            return interaction.editReply("Only server admins (Manage Server permission) can link someone else.");
        const target = forOther ? member : interaction.user;
        const picked = interaction.options.getString("player");
        const typed = interaction.options.getString("steam");
        let steamid = null;
        if (picked) {
            // autocomplete sends the SteamID64; if they typed a name without picking, look it up
            steamid = /^7656119\d{10}$/.test(picked) ? picked : (await stats.searchPlayers(picked))[0]?.steamid;
            if (!steamid)
                return interaction.editReply(`No player called **${picked}** has played a match yet. Use the \`steam:\` option with your profile link instead.`);
        } else if (typed) {
            steamid = await parseSteam(typed);
            if (!steamid)
                return interaction.editReply(
                    "I couldn't read a Steam ID from that. Paste a profile link with the number in it " +
                    "(steamcommunity.com/profiles/7656…) or your SteamID64." +
                    (process.env.STEAM_API_KEY ? "" : " Custom /id/ links need a Steam API key on the bot."));
        } else {
            return interaction.editReply("Pick your in-game name in `player:` (start typing it), or paste your Steam profile link in `steam:`.");
        }
        try {
            // admins override an existing link (e.g. fixing a mistake); players can't take someone else's
            const res = await stats.link(target.id, steamid, forOther && isAdmin);
            const name = `**${res.name || steamid}**`;
            const notes = [];
            if (!res.name) notes.push("No matches with that account yet, but they'll count once they play.");
            if (res.replaced_discord_id) notes.push(`That player was linked to <@${res.replaced_discord_id}> before; that link is removed.`);
            if (res.previous_steamid) notes.push(`${forOther ? "They were" : "You were"} linked to another player before; that's replaced.`);
            await interaction.editReply((forOther
                ? `Linked ${target} to ${name}.`
                : `Linked to ${name}. \`/stats\` now shows you by default, and you're tagged when you're MVP.`) +
                (notes.length ? "\n" + notes.join(" ") : ""));
        } catch (err) {
            if (err.status === 409)
                return interaction.editReply("That player is already linked to someone else's Discord account. If it's really you, ask them to run `/unlink` first, or ask an admin.");
            throw err;
        }
    },
    parseSteam
};
