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
        .setColor("Green");

    // Discord link buttons only accept http(s) URLs, so steam:// links can't be used directly.
    // The buttons go to the redirect page (docs/connect.html on GitHub Pages), which opens Steam.
    // Server details go in the #fragment so they never reach the web server.
    let row = null;
    if (/^https?:\/\//.test(config.connectPageUrl || "")) {
        const pageUrl = mode => {
            const params = new URLSearchParams({ ip: server.address, mode });
            if (server.password) params.set("pw", server.password);
            return `${config.connectPageUrl}#${params}`;
        };

        row = new ActionRowBuilder().addComponents(
            new ButtonBuilder()
                .setStyle(ButtonStyle.Link)
                .setLabel("CS2 Open")
                .setURL(pageUrl("open")),
            new ButtonBuilder()
                .setStyle(ButtonStyle.Link)
                .setLabel("CS2 Closed")
                .setURL(pageUrl("closed"))
        );
    }

    return { embed, row };
}

module.exports = { getServerInfo, buildConnectMessage };
