const { SlashCommandBuilder } = require("discord.js");
const { getStatus, statusMessage } = require("../lib/serverStatus");

module.exports = {
    data: new SlashCommandBuilder()
        .setName("status")
        .setDescription("Is the CS2 server up, what map, and who's on"),

    async execute(interaction) {
        await interaction.deferReply({ ephemeral: true });
        await interaction.editReply(statusMessage(await getStatus()));
    }
};
