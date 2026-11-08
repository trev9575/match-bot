const { SlashCommandBuilder } = require("discord.js");
const stats = require("../lib/stats");
const { recapEmbed } = require("../lib/communityEmbeds");

module.exports = {
    data: new SlashCommandBuilder()
        .setName("recap")
        .setDescription("Highlights from the last week (or more) of matches")
        .addIntegerOption((o) => o.setName("days").setDescription("How many days back (default 7)").setMinValue(1).setMaxValue(90)),

    async execute(interaction) {
        await interaction.deferReply();
        const days = interaction.options.getInteger("days") || 7;
        const rc = await stats.recap(days);
        if (!rc.matches) return interaction.editReply(`No matches in the last ${days} day${days === 1 ? "" : "s"}.`);
        const e = recapEmbed(rc).setTitle(days === 7 ? "Weekly recap" : `Last ${days} days`);
        await interaction.editReply({ embeds: [e] });
    }
};
