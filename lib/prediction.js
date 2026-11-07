// Win prediction posted once teams are set: each team's chance from everyone's Elo,
// and how much Elo each player stands to win or lose.
const { EmbedBuilder } = require("discord.js");
const stats = require("./stats");

const range = ([a, b], sign) => {
    const f = (v) => `${sign}${Math.abs(v)}`;
    return a === b ? f(a) : `${f(Math.min(Math.abs(a), Math.abs(b)))} to ${f(Math.max(Math.abs(a), Math.abs(b)))}`;
};

function bar(p, width = 20) {
    const n = Math.max(0, Math.min(width, Math.round(p * width)));
    return "█".repeat(n) + "░".repeat(width - n);
}

function line(p) {
    if (!p.linked) return `<@${p.id}>: not linked, counts as a new player`;
    if (!p.placed) return `<@${p.id}>: in placement (${p.elo_matches} played)`;
    return `<@${p.id}> ${p.rank_short} ${p.elo}\n  win **${range(p.elo_win, "+")}** · lose **${range(p.elo_loss, "−")}**`;
}

// team1/team2: Discord ids. name1/name2: team names. Returns an embed, or null if stats are down.
async function predictionEmbed(team1, team2, name1 = "Team 1", name2 = "Team 2") {
    let pr;
    try {
        pr = await stats.predict(team1, team2);
    } catch (err) {
        console.error("[prediction]", err.message);
        return null;
    }
    const byId = Object.fromEntries(pr.players.map((p) => [p.id, p]));
    const pa = pr.win_prob_a, pb = pr.win_prob_b;
    const fav = Math.abs(pa - 0.5) < 0.03 ? "Dead even." : `${pa > pb ? name1 : name2} are favored.`;
    const e = new EmbedBuilder()
        .setTitle("Win prediction")
        .setColor(0x8b5cf6)
        .setDescription(
            `**${name1} ${Math.round(pa * 100)}%** \`${bar(pa)}\` **${Math.round(pb * 100)}% ${name2}**\n${fav} ` +
            "Elo at stake is a range because your own performance (Rating 3.0) scales it up to 30% either way.")
        .addFields(
            { name: name1, value: team1.map((id) => line(byId[id] || { id, linked: false })).join("\n").slice(0, 1024) || "–", inline: true },
            { name: name2, value: team2.map((id) => line(byId[id] || { id, linked: false })).join("\n").slice(0, 1024) || "–", inline: true }
        )
        .setFooter({ text: "From everyone's current Elo (Glicko-2). Underdogs win more and lose less." });
    return e;
}

module.exports = { predictionEmbed };
