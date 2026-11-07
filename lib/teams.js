// Elo-balanced random teams for the /startmatch lobby.
const { EmbedBuilder, PermissionFlagsBits } = require("discord.js");
const stats = require("./stats");

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
        notes.push(`${result.unlinked.map((id) => `<@${id}>`).join(", ")} ${result.unlinked.length === 1 ? "hasn't" : "haven't"} run \`/link\`, so ${result.unlinked.length === 1 ? "they count" : "they each count"} as ${DEFAULT_ELO}.`);
    e.setDescription(notes.join("\n"));
    return e;
}

module.exports = { balance, balancedTeams, balanceEmbed, canRun };
