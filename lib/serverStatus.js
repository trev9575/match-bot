// Live CS2 server status: asks the game server itself (Steam's A2S query, the same thing the
// server browser uses) for the map and who's on, then keeps one status message and the bot's
// "Playing ..." line up to date. Read-only: it never logs in or sends commands to the server.
const dgram = require("dgram");
const { EmbedBuilder, ActivityType } = require("discord.js");
const { getServerInfo, buildConnectMessage } = require("./serverInfo");
const stats = require("./stats");

const A2S_INFO = Buffer.concat([Buffer.from([0xff, 0xff, 0xff, 0xff, 0x54]), Buffer.from("Source Engine Query\0")]);
const A2S_PLAYER = Buffer.from([0xff, 0xff, 0xff, 0xff, 0x55]);

// Send a request; if the server answers with a challenge (0x41), resend with it appended.
function query(host, port, request, wantType, challengeAppend, timeout = 2500) {
    return new Promise((resolve, reject) => {
        const sock = dgram.createSocket("udp4");
        let done = false, tries = 0;
        const finish = (err, val) => {
            if (done) return;
            done = true;
            clearTimeout(timer);
            try { sock.close(); } catch (_) {}
            err ? reject(err) : resolve(val);
        };
        const timer = setTimeout(() => finish(new Error("timeout")), timeout);
        sock.on("error", (e) => finish(e));
        sock.on("message", (msg) => {
            if (msg.length < 5 || msg.readInt32LE(0) !== -1) return;
            const type = msg[4];
            if (type === 0x41 && tries < 3) {
                tries++;
                const ch = msg.subarray(5, 9);
                sock.send(challengeAppend(ch), port, host);
            } else if (type === wantType) {
                finish(null, msg.subarray(5));
            }
        });
        sock.send(request, port, host, (e) => e && finish(e));
    });
}

function reader(buf) {
    let i = 0;
    return {
        byte: () => buf[i++],
        short: () => { const v = buf.readUInt16LE(i); i += 2; return v; },
        long: () => { const v = buf.readInt32LE(i); i += 4; return v; },
        float: () => { const v = buf.readFloatLE(i); i += 4; return v; },
        str: () => { const end = buf.indexOf(0, i); const s = buf.toString("utf8", i, end < 0 ? buf.length : end); i = end < 0 ? buf.length : end + 1; return s; },
        left: () => buf.length - i
    };
}

async function a2sInfo(host, port) {
    const body = await query(host, port, A2S_INFO, 0x49, (ch) => Buffer.concat([A2S_INFO, ch]));
    const r = reader(body);
    r.byte();                                  // protocol
    const name = r.str(), map = r.str();
    r.str(); r.str(); r.short();               // folder, game, app id
    const players = r.byte(), maxPlayers = r.byte(), bots = r.byte();
    r.byte(); r.byte();                        // server type, environment
    const password = r.byte() === 1;
    return { name, map, players, maxPlayers, bots, password };
}

async function a2sPlayers(host, port) {
    const req = (ch) => Buffer.concat([A2S_PLAYER, ch]);
    const body = await query(host, port, req(Buffer.from([0xff, 0xff, 0xff, 0xff])), 0x44, req);
    const r = reader(body);
    const n = r.byte(), out = [];
    for (let k = 0; k < n && r.left() > 0; k++) {
        r.byte();
        const name = r.str(), score = r.long(), duration = r.float();
        if (name) out.push({ name, score, duration });
    }
    return out;
}

// Where to send queries: STATUS_QUERY_ADDRESS, else the LAN address (the bot usually runs on the
// same network as the game server, where the public address may not loop back), else the public one.
function queryTarget() {
    const raw = (process.env.STATUS_QUERY_ADDRESS || process.env.CS2_LAN_ADDRESS || "").trim();
    const server = getServerInfo();
    const addr = raw ? (raw.includes(":") ? raw : `${raw}:${server?.address.split(":")[1] || 27015}`) : server?.address;
    if (!addr) return null;
    const [host, port] = addr.split(":");
    return { host, port: parseInt(port || "27015", 10) };
}

async function getStatus() {
    const t = queryTarget();
    if (!t) return { configured: false };
    try {
        const info = await a2sInfo(t.host, t.port);
        let list = [];
        try { list = await a2sPlayers(t.host, t.port); } catch (_) {}
        return { configured: true, online: true, ...info, list, at: new Date() };
    } catch (err) {
        return { configured: true, online: false, error: err.message, at: new Date() };
    }
}

const mapName = (m) => (m || "?").replace(/^(de|cs|ar)_/, "").replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
const mins = (s) => (s >= 3600 ? `${Math.floor(s / 3600)}h${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}` : `${Math.floor(s / 60)}m`);

function statusEmbed(s) {
    const e = new EmbedBuilder().setTimestamp(s.at || new Date());
    if (!s.configured) return e.setTitle("CS2 server").setDescription("No server address configured.").setColor(0x6b7280);
    if (!s.online) return e.setTitle("🔴 CS2 server offline").setDescription("The server didn't answer. It may be restarting or changing maps.").setColor(0xef4444);
    const humans = Math.max(0, s.players - (s.bots || 0));
    e.setTitle(`🟢 ${mapName(s.map)} · ${humans}/${s.maxPlayers} players`)
        .setColor(humans ? 0x22c55e : 0x3b82f6)
        .setDescription(s.name ? `**${s.name}**` : null);
    if (s.list.length) {
        const sorted = s.list.slice().sort((a, b) => b.score - a.score);
        e.addFields({ name: "On the server", value: sorted.map((p) => `${p.name.replace(/[*_`~|]/g, "")} · ${mins(p.duration)}`).join("\n").slice(0, 1024) });
    } else if (!humans) {
        e.addFields({ name: "On the server", value: "Nobody yet. Be the first." });
    }
    e.setFooter({ text: "Updates every minute" });
    return e;
}

function statusMessage(s) {
    const msg = { embeds: [statusEmbed(s)], components: [] };
    const connect = buildConnectMessage();
    if (connect && connect.row && s.online) msg.components.push(connect.row);
    return msg;
}

// Keeps a status message in STATUS_CHANNEL_ID (reused across restarts) and the bot's presence fresh
function startServerStatus(client) {
    const channelId = process.env.STATUS_CHANNEL_ID;
    const every = (parseInt(process.env.STATUS_SECONDS || "60", 10) || 60) * 1000;
    if (!queryTarget()) {
        console.log("[status] no server address configured: live status is off");
        return;
    }
    let message = null, last = "", busy = false;
    const tick = async () => {
        if (busy) return;
        busy = true;
        try {
            const s = await getStatus();
            client.serverStatus = s;
            const humans = s.online ? Math.max(0, s.players - (s.bots || 0)) : 0;
            const presence = s.online ? `${mapName(s.map)} · ${humans}/${s.maxPlayers}` : "Server offline";
            client.user.setPresence({ activities: [{ name: presence, type: ActivityType.Watching }], status: s.online ? "online" : "idle" });
            if (channelId) {
                const key = JSON.stringify([s.online, s.map, s.players, s.list?.map((p) => p.name)]);
                if (!message) {
                    const channel = await client.channels.fetch(channelId);
                    const saved = await stats.kvGet("status_message").catch(() => null);
                    if (saved) message = await channel.messages.fetch(saved).catch(() => null);
                    if (!message) {
                        message = await channel.send(statusMessage(s));
                        await stats.kvSet("status_message", message.id).catch(() => {});
                        last = key;
                    }
                }
                // edit when something changed, and at least every 10 minutes so the timestamp stays fresh
                if (key !== last || Date.now() - (message.editedTimestamp || message.createdTimestamp) > 600000) {
                    await message.edit(statusMessage(s));
                    last = key;
                }
            }
        } catch (err) {
            console.error("[status]", err.message);
            if (/Unknown Message/i.test(err.message)) message = null;
        } finally {
            busy = false;
        }
    };
    setInterval(tick, every);
    tick();
}

module.exports = { startServerStatus, getStatus, statusMessage, a2sInfo, a2sPlayers };
