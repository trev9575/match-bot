const { SlashCommandBuilder } = require("discord.js");
const stats = require("../lib/stats");

module.exports = {
    data: new SlashCommandBuilder()
        .setName("unlink")
        .setDescription("Remove the link between your Discord and Steam accounts"),

    async execute(interaction) {
        await interaction.deferReply({ ephemeral: true });
        await stats.unlink(interaction.user.id);
        await interaction.editReply("Unlinked. Your stats are still recorded under your Steam account.");
    }
};
