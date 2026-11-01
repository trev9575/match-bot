const { SlashCommandBuilder } = require("discord.js");
const { buildConnectMessage } = require("../lib/serverInfo");

module.exports = {
    data: new SlashCommandBuilder()
        .setName("connect")
        .setDescription("Show how to join the CS2 server"),

    async execute(interaction) {
        const connect = buildConnectMessage();
        if (!connect)
            return interaction.reply({ content: "No CS2 server is configured.", ephemeral: true });

        await interaction.reply({
            embeds: [connect.embed],
            components: connect.row ? [connect.row] : []
        });
    }
};
