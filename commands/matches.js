const { SlashCommandBuilder, EmbedBuilder } = require("discord.js");
const stats = require("../lib/stats");
const { mapName, when } = require("../lib/statsEmbeds");

module.exports = {
    data: new SlashCommandBuilder()
        .setName("matches")
        .setDescription("Recent CS2 matches"),

    async execute(interaction) {
        await interaction.deferReply();
        const { matches, total } = await stats.matches(10);
        if (!matches.length) return interaction.editReply("No matches recorded yet.");
        const lines = matches.map((m) => {
            const label = `#${m.id} ${mapName(m.map)} ${m.score_a}:${m.score_b}`;
            const url = stats.pageUrl(`/match/${m.id}`);
            return `${url ? `[${label}](${url})` : `**${label}**`} · ${when(m.played_at)}`;
        });
        const embed = new EmbedBuilder().setColor(0x3b82f6).setTitle("Recent matches")
            .setDescription(lines.join("\n"))
            .setFooter({ text: `${total} matches total · /match id:<number> for a scorecard` });
        await interaction.editReply({ embeds: [embed] });
    }
};
