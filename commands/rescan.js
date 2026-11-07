const { SlashCommandBuilder, PermissionFlagsBits } = require("discord.js");
const stats = require("../lib/stats");

module.exports = {
    data: new SlashCommandBuilder()
        .setName("rescan")
        .setDescription("(admin) Check the MatchZy demo folder for new matches right now")
        .setDefaultMemberPermissions(PermissionFlagsBits.ManageGuild),

    async execute(interaction) {
        await interaction.deferReply({ ephemeral: true });
        const { new: added } = await stats.rescan();
        await interaction.editReply(added ? `Imported ${added} new match(es). Scorecards will post shortly.` : "No new matches found.");
    }
};
