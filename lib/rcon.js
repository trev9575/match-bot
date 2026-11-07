// Minimal Source RCON client (the protocol CS2 dedicated servers speak over TCP).
// https://developer.valvesoftware.com/wiki/Source_RCON_Protocol
const net = require("net");

const AUTH = 3, EXEC = 2, AUTH_RESPONSE = 2;

function packet(id, type, body) {
    const b = Buffer.from(body, "utf8");
    const buf = Buffer.alloc(14 + b.length);
    buf.writeInt32LE(10 + b.length, 0);
    buf.writeInt32LE(id, 4);
    buf.writeInt32LE(type, 8);
    b.copy(buf, 12);
    return buf;   // last two bytes are already 0 (body + empty string terminators)
}

function rconConfig() {
    const password = process.env.CS2_RCON_PASSWORD || "";
    if (!password) return null;
    return {
        host: process.env.CS2_RCON_HOST || process.env.CS2_SERVER_IP || "",
        port: parseInt(process.env.CS2_RCON_PORT || process.env.CS2_SERVER_PORT || "27015", 10),
        password
    };
}

// Runs one command and resolves with its text output.
function rcon(command, cfg = rconConfig(), timeoutMs = 8000) {
    return new Promise((resolve, reject) => {
        if (!cfg || !cfg.host) return reject(new Error("RCON isn't configured (CS2_RCON_PASSWORD / CS2_RCON_HOST)"));
        const sock = net.connect(cfg.port, cfg.host);
        let buf = Buffer.alloc(0), authed = false, out = "", idle = null, done = false;
        const finish = (err, val) => {
            if (done) return;
            done = true;
            clearTimeout(timer);
            clearTimeout(idle);
            sock.destroy();
            err ? reject(err) : resolve(val);
        };
        const timer = setTimeout(() => finish(new Error(authed
            ? "The server didn't answer the command in time"
            : "Couldn't reach RCON (check CS2_RCON_HOST/PORT, and that the server allows RCON)")), timeoutMs);
        sock.on("error", (e) => finish(new Error(`RCON connection failed: ${e.message}`)));
        sock.on("connect", () => sock.write(packet(1, AUTH, cfg.password)));
        sock.on("data", (chunk) => {
            buf = Buffer.concat([buf, chunk]);
            while (buf.length >= 4) {
                const size = buf.readInt32LE(0);
                if (buf.length < size + 4) break;
                const id = buf.readInt32LE(4), type = buf.readInt32LE(8);
                const body = buf.toString("utf8", 12, 4 + size - 2);
                buf = buf.subarray(4 + size);
                if (!authed) {
                    if (type !== AUTH_RESPONSE) continue;          // empty packet some servers send first
                    if (id === -1) return finish(new Error("RCON password was rejected"));
                    authed = true;
                    sock.write(packet(2, EXEC, command));
                } else if (id === 2) {
                    out += body;
                    clearTimeout(idle);                            // output can span several packets
                    idle = setTimeout(() => finish(null, out), 400);
                }
            }
        });
    });
}

module.exports = { rcon, rconConfig, packet };
