const { ActionRowBuilder, ButtonBuilder, ButtonStyle, EmbedBuilder } = require("discord.js");
const config = require("../config.json");

// Server details come from env first (so the password stays out of git), then config.json
function getServerInfo() {
    const ip = process.env.CS2_SERVER_IP || config.cs2ServerIp;
    const port = process.env.CS2_SERVER_PORT || config.cs2ServerPort || "27015";
    const password = process.env.CS2_SERVER_PASSWORD || "";

    if (!ip) return null;
    // Optional LAN address for people on the server's own network (their router can't loop back)
    const lanRaw = (process.env.CS2_LAN_ADDRESS || "").trim();
    const lan = lanRaw ? (lanRaw.includes(":") ? lanRaw : `${lanRaw}:${port}`) : null;
    return { address: `${ip}:${port}`, password, lan };
}

// The stats website's /join page, if STATS_PUBLIC_URL is set: one button that works whether CS2 is open or not
function joinPageUrl(server) {
    const base = (process.env.STATS_PUBLIC_URL || "").replace(/\/+$/, "");
    if (!/^https?:\/\//.test(base)) return null;
    const params = new URLSearchParams({ ip: server.address });
    if (server.password) params.set("pw", server.password);
    if (server.lan) params.set("lan", server.lan);
    return `${base}/join#${params}`;
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
    if (server.lan)
        embed.addFields({ name: "On the server's home network?", value: "```\n" + command.replace(server.address, server.lan) + "\n```" });

    // Discord link buttons only accept http(s) URLs, so steam:// links can't be used directly.
    // With the stats site set up, one "Join Server" button opens its /join page, which hands off
    // to Steam. Otherwise the two buttons use the GitHub Pages redirect (docs/connect.html).
    // Server details go in the #fragment so they never reach the web server.
    let row = null;
    const join = joinPageUrl(server);
    if (join) {
        row = new ActionRowBuilder().addComponents(
            new ButtonBuilder().setStyle(ButtonStyle.Link).setLabel("Join Server").setURL(join)
        );
    } else if (/^https?:\/\//.test(config.connectPageUrl || "")) {
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
