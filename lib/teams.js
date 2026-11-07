// Elo-balanced random teams, and handing the final teams to MatchZy on the server.
const { ActionRowBuilder, EmbedBuilder, StringSelectMenuBuilder, PermissionFlagsBits } = require("discord.js");
const stats = require("./stats");
const { rcon, rconConfig } = require("./rcon");

const DEFAULT_ELO = 1500;
const TOLERANCE = 15;          // splits within this many Elo of the best one are equally good; pick one at random

function canRun(interaction, match) {
    return interaction.user.id === match.adminId ||
        interaction.memberPermissions?.has(PermissionFlagsBits.ManageGuild);
}

function displayName(guild, id) {
    return guild?.members.cache.get(id)?.displayName || "Player";
}

// Teams are named after their captain's in-game name (what MatchZy shows), else their Discord name
function captainName(ratings, guild, id) {
    return ratings[id]?.name || displayName(guild, id);
}

// Every way to pick k items from arr (lobby is at most 20 people, so this stays small)
function* combinations(arr, k, start = 0, picked = []) {
    if (picked.length === k) { yield picked.slice(); return; }
    for (let i = start; i <= arr.length - (k - picked.length); i++) {
        picked.push(arr[i]);
        yield* combinations(arr, k, i + 1, picked);
        picked.pop();
    }
}

// players: [{id, elo}]. Two random captains on opposite teams, then the split of
// everyone else that makes the teams' average Elo closest, with a little randomness
// among near-equal splits so the same ten people don't always get the same teams.
function balance(players, rng = Math.random) {
    if (players.length < 2) throw new Error("Need at least 2 players");
    const shuffled = players.slice().sort(() => rng() - 0.5);
    const [c1, c2, ...rest] = shuffled;
    const size1 = Math.ceil(players.length / 2) - 1;           // team 1 gets the extra player if odd
    const avg = (team) => team.reduce((a, p) => a + p.elo, 0) / team.length;
    const options = [];
    let best = Infinity;
    for (const pick of combinations(rest, size1)) {
        const t1 = [c1, ...pick];
        const t2 = [c2, ...rest.filter((p) => !pick.includes(p))];
        const diff = Math.abs(avg(t1) - avg(t2));
        options.push({ t1, t2, diff });
        best = Math.min(best, diff);
    }
    const good = options.filter((o) => o.diff <= best + TOLERANCE);
    const choice = good[Math.floor(rng() * good.length)];
    return {
        captains: [c1.id, c2.id],
        team1: choice.t1.map((p) => p.id),
        team2: choice.t2.map((p) => p.id),
        avg1: Math.round(avg(choice.t1)),
        avg2: Math.round(avg(choice.t2))
    };
}

// Looks up linked Steam accounts + Elo for Discord ids. Never throws: if the stats
// service is down, everyone counts as 1500 and the caller is told.
async function ratingsFor(ids) {
    try {
        const { players } = await stats.discordRatings(ids);
        return { ratings: players, ok: true };
    } catch (err) {
        return { ratings: {}, ok: false, error: err.message };
    }
}

async function balancedTeams(ids) {
    const { ratings, ok } = await ratingsFor(ids);
    const result = balance(ids.map((id) => ({ id, elo: ratings[id]?.elo ?? DEFAULT_ELO })));
    return { ...result, ratings, statsOk: ok, unlinked: ids.filter((id) => !ratings[id]) };
}

function balanceEmbed(result, guild) {
    const line = (id) => {
        const r = result.ratings[id];
        const cap = result.captains.includes(id) ? " (C)" : "";
        return `<@${id}>${cap}: ` + (r ? `${r.rank_short} · ${r.elo}` : `not linked · ${DEFAULT_ELO}`);
    };
    const e = new EmbedBuilder()
        .setTitle("🎲 Randomized teams, balanced by Elo")
        .setColor(0x3b82f6)
        .addFields(
            { name: `Team ${captainName(result.ratings, guild, result.captains[0])} · avg ${result.avg1}`, value: result.team1.map(line).join("\n"), inline: true },
            { name: `Team ${captainName(result.ratings, guild, result.captains[1])} · avg ${result.avg2}`, value: result.team2.map(line).join("\n"), inline: true }
        );
    const notes = [`Average Elo differs by ${Math.abs(result.avg1 - result.avg2)}.`];
    if (!result.statsOk) notes.push("⚠️ Couldn't reach the stats service, so everyone counted as 1500.");
    else if (result.unlinked.length)
        notes.push(`${result.unlinked.map((id) => `<@${id}>`).join(", ")} ${result.unlinked.length === 1 ? "hasn't" : "haven't"} run \`/link\`, so ${result.unlinked.length === 1 ? "they count" : "they each count"} as ${DEFAULT_ELO}. Everyone needs to be linked before teams can be sent to the server.`);
    e.setDescription(notes.join("\n"));
    return e;
}

// ---------------------------------------------------------------- MatchZy hand-off

// MAP_POOL="de_mirage,de_inferno,Wrecked=3070288532"  (label=workshop id for workshop maps)
function mapPool() {
    const raw = process.env.MAP_POOL || "de_ancient,de_anubis,de_dust2,de_inferno,de_mirage,de_nuke,de_overpass,de_train";
    return raw.split(",").map((s) => s.trim()).filter(Boolean).slice(0, 24).map((entry) => {
        const [label, value] = entry.includes("=") ? entry.split("=").map((x) => x.trim()) : [entry, entry];
        return { label: label.replace(/^de_/, "").replace(/\b\w/g, (c) => c.toUpperCase()), value };
    });
}

function configBaseUrl() {
    // MatchZy downloads the match setup from here: by default the public site, else the LAN address
    return (process.env.MATCHZY_CONFIG_URL || process.env.STATS_PUBLIC_URL || process.env.STATS_API_URL || "").replace(/\/+$/, "");
}

// Step 1: check everyone is linked, then ask which map
async function promptSendTeams(interaction, match) {
    if (!canRun(interaction, match))
        return interaction.reply({ content: "Only the person who started the match can send teams to the server.", ephemeral: true });
    if (!match.team1.length || !match.team2.length)
        return interaction.reply({ content: "Pick the teams first.", ephemeral: true });
    await interaction.deferReply({ ephemeral: true });
    const ids = [...match.team1, ...match.team2];
    const { ratings, ok, error } = await ratingsFor(ids);
    if (!ok) return interaction.editReply(`Couldn't reach the stats service: ${error}`);
    const missing = ids.filter((id) => !ratings[id]);
    if (missing.length)
        return interaction.editReply(
            `${missing.map((id) => `<@${id}>`).join(", ")} still ${missing.length === 1 ? "needs" : "need"} to run \`/link\` with their Steam profile. ` +
            "Once a match is loaded, MatchZy kicks anyone who isn't on a team, so everyone has to be linked first. " +
            "Click **Send teams to server** again after they've linked.");
    match.ratings = ratings;
    const menu = new StringSelectMenuBuilder()
        .setCustomId(`sendmap:${match.id}`)
        .setPlaceholder("Which map?")
        .addOptions(
            { label: "Keep the current map", value: "__current__", description: rconConfig() ? "Asks the server which map it's on" : "Needs RCON set up on the bot" },
            ...mapPool().map((m) => ({ label: m.label, value: m.value.slice(0, 100) }))
        );
    return interaction.editReply({
        content: "Teams look good: everyone is linked. Pick the map; the knife round decides sides.",
        components: [new ActionRowBuilder().addComponents(menu)]
    });
}

async function currentMap() {
    const out = await rcon("status");
    const m = out.match(/\[1:\s*([\w\-./]+)\s*\|/) || out.match(/^map\s*:\s*([\w\-./]+)/m);
    if (!m) throw new Error("Couldn't read the current map from the server");
    return m[1].split("/").pop();
}

// Step 2: build the match setup, store it on the stats service, and load it on the server
async function sendTeams(interaction, match) {
    if (!canRun(interaction, match))
        return interaction.reply({ content: "Only the person who started the match can send teams.", ephemeral: true });
    await interaction.deferUpdate();
    let map = interaction.values[0];
    try {
        if (map === "__current__") map = await currentMap();
    } catch (err) {
        return interaction.editReply({ content: `${err.message}. Pick a map from the list instead.`, components: interaction.message.components });
    }
    const ratings = match.ratings || (await ratingsFor([...match.team1, ...match.team2])).ratings;
    const team = (ids, captain) => ({
        name: captainName(ratings, interaction.guild, captain),
        players: Object.fromEntries(ids.filter((id) => ratings[id]).map((id) => [ratings[id].steamid, ratings[id].name]))
    });
    const [c1, c2] = match.captains;
    let made;
    try {
        made = await stats.makeMatchConfig({ team1: team(match.team1, c1), team2: team(match.team2, c2), map });
    } catch (err) {
        return interaction.editReply({ content: `Couldn't create the match setup: ${err.message}`, components: [] });
    }
    const url = configBaseUrl() + made.path;
    const command = `matchzy_loadmatch_url "${url}"`;
    const summary = `**${made.config.team1.name}** vs **${made.config.team2.name}** on **${map}** (match setup #${made.matchid}). ` +
        "Knife round decides sides. Only these players can join a team until the match ends.";

    if (rconConfig()) {
        try {
            const out = await rcon(command);
            await interaction.editReply({ content: "Sent to the server.", components: [] });
            await interaction.channel.send(`🎮 Teams sent to the CS2 server: ${summary}` +
                (/error|fail|invalid/i.test(out) ? `\n⚠️ Server said: \`${out.trim().slice(0, 300)}\`` : ""));
            return;
        } catch (err) {
            return interaction.editReply({
                content: `RCON didn't work (${err.message}). Paste this into the server console in AMP instead:\n\`\`\`\n${command}\n\`\`\``,
                components: []
            });
        }
    }
    await interaction.editReply({
        content: `Paste this into the CS2 server console (AMP › Console):\n\`\`\`\n${command}\n\`\`\`\n${summary}\n` +
            "_Tip: set CS2_RCON_PASSWORD on the bot and it'll send this by itself._",
        components: []
    });
}

module.exports = { balance, balancedTeams, balanceEmbed, promptSendTeams, sendTeams, canRun, mapPool, currentMap };
