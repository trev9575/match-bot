const { SlashCommandBuilder } = require("discord.js");
const matchNight = require("../lib/matchNight");
const { parseWhen, TZ } = require("../lib/time");

module.exports = {
    data: new SlashCommandBuilder()
        .setName("matchnight")
        .setDescription("Post a match night signup (In / Maybe / Can't)")
        .addStringOption((o) => o.setName("when").setDescription('e.g. "8pm", "fri 9pm", "tomorrow 8:30pm", "11/14 8pm"').setRequired(true))
        .addIntegerOption((o) => o.setName("players").setDescription("How many we need (default 10)").setMinValue(2).setMaxValue(20))
        .addStringOption((o) => o.setName("note").setDescription("Anything else (maps, format, ...)").setMaxLength(200)),
    buttonPrefix: "mn",

    async execute(interaction) {
        const at = parseWhen(interaction.options.getString("when"));
        if (!at)
            return interaction.reply({ content: `Couldn't read that time. Try "8pm", "fri 9pm", "tomorrow 8:30pm" or "11/14 8pm" (${TZ} time).`, ephemeral: true });
        if (at.getTime() < Date.now() - 60000)
            return interaction.reply({ content: "That time has already passed.", ephemeral: true });
        await matchNight.create(interaction, {
            at,
            needed: matchNight.parseNeeded(interaction.options.getInteger("players")),
            note: interaction.options.getString("note")
        });
    },

    handleButton: (interaction, client) => matchNight.handleButton(interaction, client)
};
