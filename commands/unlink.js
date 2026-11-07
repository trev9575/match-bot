const { SlashCommandBuilder, PermissionFlagsBits } = require("discord.js");
const stats = require("../lib/stats");

module.exports = {
    data: new SlashCommandBuilder()
        .setName("unlink")
        .setDescription("Remove the link between your Discord account and your CS2 player")
        .addUserOption((o) => o.setName("member").setDescription("(admins) Unlink someone else instead of yourself")),

    async execute(interaction) {
        await interaction.deferReply({ ephemeral: true });
        const member = interaction.options.getUser("member");
        const forOther = member && member.id !== interaction.user.id;
        if (forOther && !interaction.memberPermissions?.has(PermissionFlagsBits.ManageGuild))
            return interaction.editReply("Only server admins (Manage Server permission) can unlink someone else.");
        await stats.unlink((forOther ? member : interaction.user).id);
        await interaction.editReply(forOther
            ? `Unlinked ${member}. Their stats are still recorded under their Steam account.`
            : "Unlinked. Your stats are still recorded under your Steam account.");
    }
};
