// Match night signups: one message with In / Maybe / Can't buttons. Pings the In list when the
// head count is reached, reminds them before start, and opens a /startmatch lobby with everyone
// who's In at start time. Saved in the stats service's key/value store, so a bot restart
// doesn't lose signups.
const { ActionRowBuilder, ButtonBuilder, ButtonStyle, EmbedBuilder } = require("discord.js");
const stats = require("./stats");
const teams = require("./teams");
const { unix } = require("./time");

const REMIND_MIN = parseInt(process.env.MATCHNIGHT_REMIND_MINUTES || "30", 10) || 30;
const AUTO_LOBBY = !/^(0|false|no)$/i.test(process.env.MATCHNIGHT_AUTO_LOBBY || "true");
const KEY = "matchnights";
const nights = new Map();          // id -> night
let loaded = false;

async function load() {
    if (loaded) return;
    try {
        const raw = await stats.kvGet(KEY);
        for (const n of JSON.parse(raw || "[]")) nights.set(n.id, n);
        loaded = true;
    } catch (err) {
        console.error("[match night] couldn't load signups:", err.message);
    }
}

async function save() {
    try {
        await stats.kvSet(KEY, JSON.stringify([...nights.values()]));
    } catch (err) {
        console.error("[match night] couldn't save signups:", err.message);
    }
}

function embed(n) {
    const list = (ids) => (ids.length ? ids.map((id) => `<@${id}>`).join("\n") : "–");
    const e = new EmbedBuilder()
        .setTitle(n.closed ? "Match night (closed)" : "Match night")
        .setColor(n.closed ? 0x6b7280 : n.inn.length >= n.needed ? 0x22c55e : 0x3b82f6)
        .setDescription(
            `**<t:${unix(n.at)}:F>** (<t:${unix(n.at)}:R>)\n` +
            (n.note ? `${n.note}\n` : "") +
            (n.closed ? `\n${n.closed}` :
                `\n${n.inn.length}/${n.needed} in.` + (n.inn.length >= n.needed ? " We have enough! 🎉" : ` Need ${n.needed - n.inn.length} more.`) +
                (AUTO_LOBBY ? " A match lobby opens with everyone who's in at start time." : "")))
        .addFields(
            { name: `In (${n.inn.length})`, value: list(n.inn).slice(0, 1024), inline: true },
            { name: `Maybe (${n.maybe.length})`, value: list(n.maybe).slice(0, 1024), inline: true },
            { name: `Can't (${n.out.length})`, value: list(n.out).slice(0, 1024), inline: true })
        .setFooter({ text: `Hosted by ${n.hostName || "?"} · reminders ${REMIND_MIN} min before` });
    return e;
}

function components(n) {
    if (n.closed) return [];
    return [
        new ActionRowBuilder().addComponents(
            new ButtonBuilder().setCustomId(`mn:in:${n.id}`).setLabel("I'm in").setStyle(ButtonStyle.Success),
            new ButtonBuilder().setCustomId(`mn:maybe:${n.id}`).setLabel("Maybe").setStyle(ButtonStyle.Secondary),
            new ButtonBuilder().setCustomId(`mn:out:${n.id}`).setLabel("Can't make it").setStyle(ButtonStyle.Secondary)),
        new ActionRowBuilder().addComponents(
            new ButtonBuilder().setCustomId(`mn:open:${n.id}`).setLabel("Open Lobby Now").setStyle(ButtonStyle.Primary),
            new ButtonBuilder().setCustomId(`mn:cancel:${n.id}`).setLabel("Cancel").setStyle(ButtonStyle.Danger))
    ];
}

const message = (n) => ({ embeds: [embed(n)], components: components(n), allowedMentions: { parse: [] } });

async function create(interaction, { at, needed, note }) {
    await load();
    const n = {
        id: Date.now().toString(36), channelId: interaction.channelId, messageId: null,
        hostId: interaction.user.id, hostName: interaction.member?.displayName || interaction.user.username,
        at: at.getTime(), needed, note: note || "", inn: [interaction.user.id], maybe: [], out: [],
        pinged: false, reminded: false, closed: null
    };
    await interaction.reply(message(n));
    const msg = await interaction.fetchReply();
    n.messageId = msg.id;
    nights.set(n.id, n);
    await save();
    return n;
}

async function editMessage(client, n) {
    try {
        const ch = await client.channels.fetch(n.channelId);
        const msg = await ch.messages.fetch(n.messageId);
        await msg.edit(message(n));
    } catch (_) {}
}

const canManage = (interaction, n) => teams.canRun(interaction, { adminId: n.hostId });

async function openLobbyFor(client, n, why) {
    const ch = await client.channels.fetch(n.channelId);
    const { openLobby } = require("../commands/startmatch");
    await openLobby(client, {
        adminId: n.hostId,
        players: n.inn,
        send: (payload) => ch.send({ ...payload, allowedMentions: { users: n.inn } }),
        intro: `${why} ${n.inn.map((id) => `<@${id}>`).join(" ")}`.trim()
    });
}

async function handleButton(interaction, client) {
    await load();
    const [, action, id] = interaction.customId.split(":");
    const n = nights.get(id);
    if (!n || n.closed) return interaction.reply({ content: "This match night is over.", ephemeral: true });
    const uid = interaction.user.id;
    if (["in", "maybe", "out"].includes(action)) {
        for (const k of ["inn", "maybe", "out"]) n[k] = n[k].filter((x) => x !== uid);
        n[{ in: "inn", maybe: "maybe", out: "out" }[action]].push(uid);
        await interaction.update(message(n));
        if (!n.pinged && n.inn.length >= n.needed) {
            n.pinged = true;
            await interaction.channel.send({
                content: `We have ${n.inn.length}! ${n.inn.map((x) => `<@${x}>`).join(" ")} see you <t:${unix(n.at)}:R> (<t:${unix(n.at)}:t>).`,
                allowedMentions: { users: n.inn }
            });
        }
        await save();
        return;
    }
    if (!canManage(interaction, n))
        return interaction.reply({ content: "Only the host (or an admin) can do that.", ephemeral: true });
    if (action === "cancel") {
        n.closed = "Cancelled by the host.";
        await interaction.update(message(n));
        if (n.inn.length > 1)
            await interaction.channel.send({ content: `Match night cancelled. ${n.inn.map((x) => `<@${x}>`).join(" ")}`, allowedMentions: { users: n.inn } });
        nights.delete(n.id);
        await save();
    } else if (action === "open") {
        n.closed = "The lobby is open below.";
        await interaction.update(message(n));
        nights.delete(n.id);
        await save();
        await openLobbyFor(client, n, "Match night is starting!");
    }
}

// Called every minute: reminders, the start-time lobby, and cleanup
async function tick(client) {
    await load();
    if (!loaded) return;
    const now = Date.now();
    let changed = false;
    for (const n of [...nights.values()]) {
        try {
            if (!n.reminded && now >= n.at - REMIND_MIN * 60000 && now < n.at) {
                n.reminded = true; changed = true;
                if (n.inn.length) {
                    const ch = await client.channels.fetch(n.channelId);
                    await ch.send({ content: `⏰ Match night starts <t:${unix(n.at)}:R>. ${n.inn.map((x) => `<@${x}>`).join(" ")}` +
                        (n.maybe.length ? `\nStill a maybe: ${n.maybe.map((x) => `<@${x}>`).join(" ")}` : ""),
                        allowedMentions: { users: n.inn.concat(n.maybe) } });
                }
            }
            if (now >= n.at) {
                n.closed = AUTO_LOBBY && n.inn.length >= 2 ? "Started: the lobby is open below." : "Started.";
                nights.delete(n.id); changed = true;
                await editMessage(client, n);
                if (AUTO_LOBBY && n.inn.length >= 2 && now - n.at < 3 * 3600000)
                    await openLobbyFor(client, n, "Match night time!");
            }
        } catch (err) {
            console.error("[match night]", err.message);
        }
    }
    if (changed) await save();
}

module.exports = { create, handleButton, tick, parseNeeded: (v) => Math.max(2, Math.min(20, v || 10)) };
