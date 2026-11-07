const { SlashCommandBuilder } = require("discord.js");
const stats = require("../lib/stats");
const { matchMessage } = require("../lib/statsEmbeds");

module.exports = {
    data: new SlashCommandBuilder()
        .setName("match")
        .setDescription("Scorecard for a CS2 match (latest by default)")
        .addIntegerOption((o) => o.setName("id").setDescription("Match number (see /matches)").setMinValue(1)),

    async execute(interaction) {
        await interaction.deferReply();
        try {
            await interaction.editReply(matchMessage(await stats.match(interaction.options.getInteger("id"))));
        } catch (err) {
            if (err.status === 404) return interaction.editReply("No match with that number.");
            throw err;
        }
    }
};
