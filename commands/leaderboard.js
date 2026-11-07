const { SlashCommandBuilder } = require("discord.js");
const stats = require("../lib/stats");
const { STATS, PERIODS, leaderboardPage } = require("../lib/statsEmbeds");

const DEFAULT_MIN = parseInt(process.env.LEADERBOARD_MIN_MATCHES || "2", 10) || 1;

module.exports = {
    data: new SlashCommandBuilder()
        .setName("leaderboard")
        .setDescription("CS2 5v5 leaderboard: scroll through everyone, with a link to the full site")
        .addStringOption((o) => o.setName("stat").setDescription("What to rank by (default: Elo / rank)")
            .addChoices(...Object.entries(STATS).map(([value, [name]]) => ({ name, value }))))
        .addStringOption((o) => o.setName("period").setDescription("Time window")
            .addChoices(...Object.entries(PERIODS).map(([value, name]) => ({ name, value }))))
        .addIntegerOption((o) => o.setName("min_matches").setDescription(`Minimum matches to show (default ${DEFAULT_MIN})`).setMinValue(1)),

    // Prev/Next buttons: customId = lb:<page>:<stat>:<period>:<min>
    buttonPrefix: "lb",
    async handleButton(interaction) {
        const [, page, stat, period, min] = interaction.customId.split(":");
        const { players } = await stats.leaderboard(period);
        await interaction.update(leaderboardPage(players, { stat, period, min: parseInt(min, 10), page: parseInt(page, 10) }));
    },

    async execute(interaction) {
        const stat = interaction.options.getString("stat") || "elo";
        const period = interaction.options.getString("period") || "all";
        const min = interaction.options.getInteger("min_matches") ?? DEFAULT_MIN;
        await interaction.deferReply();
        const { players } = await stats.leaderboard(period);
        await interaction.editReply(leaderboardPage(players, { stat, period, min, page: 0 }));
    }
};
