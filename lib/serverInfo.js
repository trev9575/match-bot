const { ActionRowBuilder, ButtonBuilder, ButtonStyle, EmbedBuilder } = require("discord.js");
const config = require("../config.json");

// Server details come from env first (so the password stays out of git), then config.json
function getServerInfo() {
    const ip = process.env.CS2_SERVER_IP || config.cs2ServerIp;
    const port = process.env.CS2_SERVER_PORT || config.cs2ServerPort || "27015";
    const password = process.env.CS2_SERVER_PASSWORD || "";

    if (!ip) return null;
    return { address: `${ip}:${port}`, password };
}

// Build the "join the server" embed (and an optional link button), or null if no server is configured
function buildConnectMessage() {
    const server = getServerInfo();
    if (!server) return null;

    const command = server.password
        ? `connect ${server.address}; password ${server.password}`
        : `connect ${server.address}`;

    const embed = new EmbedBuilder()
        .setTitle("Join the CS2 Server")
        .setDescription(
            "Open the CS2 console (`~`) and paste:\n" +
            "```\n" + command + "\n```"
        )
        .addFields({ name: "Address", value: `\`${server.address}\``, inline: true })
        .setColor("Green");

    if (server.password)
        embed.addFields({ name: "Password", value: `\`${server.password}\``, inline: true });

    // Discord link buttons only accept http(s) URLs, so steam:// links can't be used directly.
    // If an http(s) redirect page is configured, offer it as a one-click button.
    let row = null;
    if (/^https?:\/\//.test(config.cs2ConnectUrl || "")) {
        row = new ActionRowBuilder().addComponents(
            new ButtonBuilder()
                .setStyle(ButtonStyle.Link)
                .setLabel("Join CS2 Server")
                .setURL(config.cs2ConnectUrl)
        );
    }

    return { embed, row };
}

module.exports = { getServerInfo, buildConnectMessage };
