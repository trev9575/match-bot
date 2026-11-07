const { SlashCommandBuilder, PermissionFlagsBits } = require("discord.js");
const stats = require("../lib/stats");

module.exports = {
    data: new SlashCommandBuilder()
        .setName("deletematch")
        .setDescription("(admin) Remove a match from the CS2 stats, e.g. a test game")
        .addIntegerOption((o) => o.setName("id").setDescription("Match number (see /matches)").setRequired(true).setMinValue(1))
        .setDefaultMemberPermissions(PermissionFlagsBits.ManageGuild),

    async execute(interaction) {
        await interaction.deferReply({ ephemeral: true });
        const id = interaction.options.getInteger("id");
        await stats.deleteMatch(id);
        await interaction.editReply(`Deleted match #${id} from the stats. (The demo file itself is untouched.)`);
    }
};
