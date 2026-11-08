// Optional map vote for a /startmatch lobby. Whoever started the match (or an admin) picks which
// maps are on the ballot: any of the maps we've played before (from the stats database), plus
// any map typed in by name for this vote only. Lobby participants then vote with buttons.
const {
    ActionRowBuilder, ButtonBuilder, ButtonStyle, EmbedBuilder, StringSelectMenuBuilder,
    ModalBuilder, TextInputBuilder, TextInputStyle
} = require("discord.js");
const stats = require("./stats");
const teams = require("./teams");

const MAX_MAPS = 20;               // 4 rows of 5 vote buttons, plus one row for "End Vote"

const pretty = (m) => (/^(de|cs|ar)_/.test(m) ? m.replace(/^(de|cs|ar)_/, "").replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase()) : m);

function state(match) {
    match.mapVote ||= { picked: [], typed: [], played: [], open: false, votes: new Map(), maps: [], message: null };
    return match.mapVote;
}

const ballot = (v) => [...new Set([...v.picked, ...v.typed])].slice(0, MAX_MAPS);

function setupMessage(match) {
    const v = state(match);
    const rows = [];
    if (v.played.length) {
        const menu = new StringSelectMenuBuilder()
            .setCustomId(`mapsel:${match.id}`)
            .setPlaceholder("Maps we've played")
            .setMinValues(0)
            .setMaxValues(Math.min(v.played.length, 25))
            .addOptions(v.played.slice(0, 25).map((m) => ({
                label: pretty(m.map), value: m.map, description: `played ${m.matches}×`, default: v.picked.includes(m.map)
            })));
        rows.push(new ActionRowBuilder().addComponents(menu));
    }
    const maps = ballot(v);
    rows.push(new ActionRowBuilder().addComponents(
        new ButtonBuilder().setCustomId(`mapadd:${match.id}`).setLabel("Type a map…").setStyle(ButtonStyle.Secondary),
        new ButtonBuilder().setCustomId(`mapclear:${match.id}`).setLabel("Clear typed maps").setStyle(ButtonStyle.Secondary).setDisabled(!v.typed.length),
        new ButtonBuilder().setCustomId(`mapstart:${match.id}`).setLabel("Start Vote").setStyle(ButtonStyle.Success).setDisabled(maps.length < 2)
    ));
    const content = "**Map vote setup** (only you see this)\n" +
        (v.played.length ? "Pick maps from the list of maps we've played" : "No maps have been played yet, so type them in") +
        `, and/or **Type a map** for anything else (typed maps are only for this vote). Up to ${MAX_MAPS}.\n` +
        (maps.length ? `On the ballot: ${maps.map((m) => `**${pretty(m)}**`).join(", ")}` : "Nothing on the ballot yet.");
    return { content, components: rows };
}

async function openSetup(interaction, match) {
    if (!teams.canRun(interaction, match))
        return interaction.reply({ content: "Only the person who started the match (or an admin) can set up a map vote.", ephemeral: true });
    const v = state(match);
    if (v.open) return interaction.reply({ content: "A map vote is already running.", ephemeral: true });
    try { v.played = (await stats.maps()).maps || []; } catch (_) { v.played = []; }
    await interaction.reply({ ...setupMessage(match), ephemeral: true });
}

async function onSelect(interaction, match) {
    if (!teams.canRun(interaction, match)) return interaction.reply({ content: "Not your vote to set up.", ephemeral: true });
    state(match).picked = interaction.values;
    await interaction.update(setupMessage(match));
}

async function onAddButton(interaction, match) {
    if (!teams.canRun(interaction, match)) return interaction.reply({ content: "Not your vote to set up.", ephemeral: true });
    const modal = new ModalBuilder().setCustomId(`mapmodal:${match.id}`).setTitle("Add maps to the vote");
    modal.addComponents(new ActionRowBuilder().addComponents(
        new TextInputBuilder().setCustomId("maps").setLabel("Map names, separated by commas")
            .setPlaceholder("e.g. Cache, de_train, Thera").setStyle(TextInputStyle.Short).setMaxLength(300).setRequired(true)));
    await interaction.showModal(modal);
}

async function onModal(interaction, match) {
    const v = state(match);
    const known = new Map(v.played.map((m) => [m.map.toLowerCase(), m.map]));
    for (const m of v.played) known.set(pretty(m.map).toLowerCase(), m.map);
    for (const raw of interaction.fields.getTextInputValue("maps").split(",")) {
        const name = raw.trim().slice(0, 40);
        if (!name) continue;
        const hit = known.get(name.toLowerCase()) || known.get(`de_${name.toLowerCase()}`);
        if (hit) { if (!v.picked.includes(hit)) v.picked.push(hit); }
        else if (!v.typed.some((t) => t.toLowerCase() === name.toLowerCase())) v.typed.push(name);
    }
    if (interaction.isFromMessage()) await interaction.update(setupMessage(match));
    else await interaction.reply({ ...setupMessage(match), ephemeral: true });
}

async function onClear(interaction, match) {
    state(match).typed = [];
    await interaction.update(setupMessage(match));
}

function counts(v) {
    const c = v.maps.map(() => 0);
    for (const i of v.votes.values()) c[i]++;
    return c;
}

function voteMessage(match, closedWinner = null) {
    const v = state(match);
    const c = counts(v);
    const rows = [];
    for (let i = 0; i < v.maps.length; i += 5) {
        rows.push(new ActionRowBuilder().addComponents(v.maps.slice(i, i + 5).map((m, j) =>
            new ButtonBuilder().setCustomId(`mapv-${i + j}:${match.id}`)
                .setLabel(`${pretty(m)} · ${c[i + j]}`.slice(0, 80))
                .setStyle(closedWinner === m ? ButtonStyle.Success : ButtonStyle.Secondary)
                .setDisabled(!v.open))));
    }
    if (v.open)
        rows.push(new ActionRowBuilder().addComponents(
            new ButtonBuilder().setCustomId(`mapend:${match.id}`).setLabel("End Vote").setStyle(ButtonStyle.Danger)));
    const voters = v.votes.size;
    const e = new EmbedBuilder()
        .setTitle(closedWinner ? `Map: ${pretty(closedWinner)}` : "Map vote")
        .setColor(closedWinner ? 0x22c55e : 0xf59e0b)
        .setDescription(closedWinner
            ? `The vote is closed. ${voters} vote${voters === 1 ? "" : "s"}.`
            : `Players in the lobby: click a map (you can change your vote). ${voters} vote${voters === 1 ? "" : "s"} so far.\n` +
              `<@${match.adminId}> ends the vote.`);
    return { embeds: [e], components: rows };
}

async function onStart(interaction, match) {
    if (!teams.canRun(interaction, match)) return interaction.reply({ content: "Not your vote to set up.", ephemeral: true });
    const v = state(match);
    v.maps = ballot(v);
    if (v.maps.length < 2) return interaction.reply({ content: "Put at least 2 maps on the ballot.", ephemeral: true });
    v.open = true;
    v.votes = new Map();
    await interaction.update({ content: `Vote started with ${v.maps.length} maps.`, components: [] });
    v.message = await interaction.channel.send(voteMessage(match));
}

async function onVote(interaction, match, idx) {
    const v = state(match);
    if (!v.open) return interaction.reply({ content: "This vote is closed.", ephemeral: true });
    if (!match.players.includes(interaction.user.id) && interaction.user.id !== match.adminId)
        return interaction.reply({ content: "Only players in the lobby can vote. Click **Participate in Match** first.", ephemeral: true });
    if (!(idx >= 0 && idx < v.maps.length)) return interaction.reply({ content: "Unknown map.", ephemeral: true });
    v.votes.set(interaction.user.id, idx);
    await interaction.update(voteMessage(match));
}

async function onEnd(interaction, match) {
    if (!teams.canRun(interaction, match))
        return interaction.reply({ content: "Only the person who started the match (or an admin) can end the vote.", ephemeral: true });
    const v = state(match);
    if (!v.open) return interaction.reply({ content: "This vote is already closed.", ephemeral: true });
    v.open = false;
    const c = counts(v);
    const top = Math.max(...c);
    const tied = v.maps.filter((_, i) => c[i] === top);
    const winner = tied[Math.floor(Math.random() * tied.length)];
    match.map = winner;
    await interaction.update(voteMessage(match, winner));
    await interaction.channel.send(`🗺️ **${pretty(winner)}** wins the map vote` +
        (tied.length > 1 ? ` (tie broken at random between ${tied.map(pretty).join(", ")})` : "") + ".");
}

// Routes every map-vote component; returns true if it handled the interaction
async function handle(interaction, action, match) {
    if (action === "mapvote") { await openSetup(interaction, match); return true; }
    if (action === "mapsel") { await onSelect(interaction, match); return true; }
    if (action === "mapadd") { await onAddButton(interaction, match); return true; }
    if (action === "mapmodal") { await onModal(interaction, match); return true; }
    if (action === "mapclear") { await onClear(interaction, match); return true; }
    if (action === "mapstart") { await onStart(interaction, match); return true; }
    if (action === "mapend") { await onEnd(interaction, match); return true; }
    if (action.startsWith("mapv-")) { await onVote(interaction, match, parseInt(action.slice(5), 10)); return true; }
    return false;
}

module.exports = { handle, pretty, MAX_MAPS };
