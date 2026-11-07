const { SlashCommandBuilder } = require("discord.js");
const stats = require("../lib/stats");
const { playerMessage } = require("../lib/statsEmbeds");

module.exports = {
    data: new SlashCommandBuilder()
        .setName("stats")
        .setDescription("A player's CS2 profile and recent matches (defaults to you)")
        .addStringOption((o) => o.setName("player").setDescription("In-game name").setAutocomplete(true))
        .addUserOption((o) => o.setName("member").setDescription("Or a Discord member who ran /link")),

    async autocomplete(interaction) {
        const q = interaction.options.getFocused();
        const list = await stats.searchPlayers(q).catch(() => []);
        await interaction.respond(list.slice(0, 25).map((p) => ({ name: p.name.slice(0, 100) || p.steamid, value: p.steamid })));
    },

    async execute(interaction) {
        const typed = interaction.options.getString("player");
        const member = interaction.options.getUser("member");
        await interaction.deferReply();

        let steamid = null;
        if (typed) {
            // autocomplete sends the SteamID64; a typed name gets looked up
            steamid = /^7656119\d{10}$/.test(typed) ? typed : (await stats.searchPlayers(typed))[0]?.steamid;
            if (!steamid) return interaction.editReply(`No player called **${typed}** has played a match yet.`);
        } else {
            const user = member || interaction.user;
            steamid = await stats.steamidForDiscord(user.id);
            if (!steamid)
                return interaction.editReply(member
                    ? `${member} hasn't linked their Steam account yet (they can run \`/link\`).`
                    : "Your Steam account isn't linked yet. Run `/link` with your Steam profile URL, or use `player:`.");
        }
        try {
            await interaction.editReply(playerMessage(await stats.player(steamid)));
        } catch (err) {
            if (err.status === 404) return interaction.editReply("That player has no matches recorded yet.");
            throw err;
        }
    }
};
