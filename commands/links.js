const { SlashCommandBuilder, PermissionFlagsBits, EmbedBuilder } = require("discord.js");
const stats = require("../lib/stats");

// Admin checklist: everyone who has played, most matches first, and who they're linked to.
// Fix any line with /link player:<name> member:@them
module.exports = {
    data: new SlashCommandBuilder()
        .setName("links")
        .setDescription("(admin) Who's linked: every player from our matches and their Discord account")
        .addStringOption((o) => o.setName("show").setDescription("Which players to list (default: everyone)")
            .addChoices({ name: "Everyone", value: "all" }, { name: "Only players who aren't linked", value: "unlinked" }))
        .setDefaultMemberPermissions(PermissionFlagsBits.ManageGuild),

    async execute(interaction) {
        await interaction.deferReply({ ephemeral: true });
        const only = interaction.options.getString("show") === "unlinked";
        const { players } = await stats.links();
        const total = players.length, linked = players.filter((p) => p.discord_id).length;
        const list = only ? players.filter((p) => !p.discord_id) : players;
        const clean = (s) => String(s || "?").replace(/([*_`~|\\])/g, "\\$1");
        const lines = list.map((p) => `${p.discord_id ? "✅" : "▫️"} **${clean(p.name)}** (${p.matches} match${p.matches === 1 ? "" : "es"}) → ` +
            (p.discord_id ? `<@${p.discord_id}>` : "not linked"));
        // stay under Discord's 4096-character description limit
        let text = "", shown = 0;
        for (const l of lines) {
            if (text.length + l.length + 1 > 3900) break;
            text += l + "\n";
            shown++;
        }
        if (!lines.length) text = only ? "Everyone who has played is linked. 🎉" : "Nobody has played a match yet.";
        const embed = new EmbedBuilder()
            .setColor(0x3b82f6)
            .setTitle(`Linked players: ${linked} of ${total}`)
            .setDescription(text)
            .setFooter({ text: (shown < lines.length ? `Showing ${shown} of ${lines.length}. ` : "") +
                "Link someone: /link player:<their in-game name> member:@them" });
        await interaction.editReply({ embeds: [embed], allowedMentions: { users: [] } });
    }
};
