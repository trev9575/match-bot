// Discord embeds for the CS2 stats commands. Tables go in ``` blocks so columns line up.
const { ActionRowBuilder, ButtonBuilder, ButtonStyle, EmbedBuilder } = require("discord.js");
const { pageUrl } = require("./stats");

const BLUE = 0x3b82f6, GREEN = 0x2ecc71, GOLD = 0xf1c40f;
const PAGE_SIZE = 10;

const num = (v) => (v == null || Number.isNaN(Number(v)) ? 0 : Number(v));
const f0 = (v) => num(v).toFixed(0);
const f1 = (v) => num(v).toFixed(1);
const f2 = (v) => num(v).toFixed(2);
const signed = (v) => (v == null ? "–" : (num(v) >= 0 ? "+" : "-") + Math.abs(num(v)).toFixed(1) + "%");
const rt = (v) => (v == null ? "–" : f2(v));
const unplaced = (p) => p.placed === false;                                  // Elo hidden until placement is done
const rankShort = (p) => (p.rank ? p.rank_short : p.elo_sort != null || p.elo != null ? "?" : "");   // "?" = placement / expired
const eloShort = (p) => (p.elo != null ? String(p.elo) : unplaced(p) ? "?" : "");
const signedInt = (d) => (d == null ? "" : (d > 0 ? "+" : "") + d);
const rankText = (p) => (p.elo == null && p.elo_sort == null ? "no matches" :
    p.elo == null ? `${p.rank_name || "Unranked"} · Elo hidden until placement is done` : `${p.rank_name} · ${p.elo} Elo`);
const mapName = (m) => (m || "?").replace(/^de_/, "").replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
const cell = (name, w = 13) => {
    const s = String(name ?? "?").replace(/`/g, "'");
    return s.length > w ? s.slice(0, w - 1) + "…" : s.padEnd(w);
};
const when = (iso) => {
    const t = Date.parse(iso || "");
    return Number.isNaN(t) ? "" : `<t:${Math.floor(t / 1000)}:f>`;
};

// What /leaderboard can rank by: key -> [label, column header, formatter]
const STATS = {
    elo: ["Elo / rank (default)", "ELO", (v) => (v == null ? "?" : f0(v))],
    rating: ["Rating 3.0", "RTG", rt],
    kd: ["K/D", "K/D", f2],
    adr: ["ADR", "ADR", f1],
    kast: ["KAST %", "KAST", (v) => f0(v) + "%"],
    swing_pr: ["Round swing / round", "SWING", signed],
    kills: ["Total kills", "KILLS", f0],
    hs_pct: ["Headshot %", "HS", (v) => f0(v) + "%"],
    entry_win_pct: ["Opening duel win %", "OPEN", (v) => f0(v) + "%"],
    clutch_wins: ["Clutches won", "CLUTCH", f0],
    util_adr: ["Utility damage / round", "UTIL", f1],
    enemies_flashed: ["Enemies flashed", "FLASH", f0],
    team_flashed: ["Teammates flashed 🙃", "TEAMFL", f0],
    win_pct: ["Win %", "WIN", (v) => f0(v) + "%"],
    firepower: ["Firepower score", "FIRE", f0],
    entrying: ["Entrying score", "ENTRY", f0],
    trading: ["Trading score", "TRADE", f0],
    clutching: ["Clutching score", "CLUTCH", f0],
    utility: ["Utility score", "UTIL", f0]
};
const PERIODS = { all: "All time", 30: "Last 30 days", 7: "Last 7 days" };

function linkButton(label, path) {
    const url = pageUrl(path);
    return url ? new ButtonBuilder().setStyle(ButtonStyle.Link).setLabel(label).setURL(url) : null;
}

function rowOf(...buttons) {
    const list = buttons.filter(Boolean);
    return list.length ? [new ActionRowBuilder().addComponents(...list)] : [];
}

// ---------------------------------------------------------------- leaderboard

// Sorts and filters the players, then builds one page (10 players) with Prev/Next buttons.
// The button IDs carry the settings, so paging works after a bot restart too.
function leaderboardPage(players, { stat = "rating", period = "all", min = 1, page = 0 }) {
    const [label, head, fmt] = STATS[stat] || STATS.rating;
    if (stat === "win_pct")
        players.forEach((p) => (p.win_pct = (100 * num(p.wins)) / Math.max(num(p.matches), 1)));
    const rows = players
        .filter((p) => num(p.matches) >= min)
        .filter((p) => (stat === "elo" ? p.elo_sort != null : p[stat] != null))   // e.g. no Rating 3.0 data
        // Elo: ranked players first, then players still in placement by their hidden Elo
        .sort((a, b) => stat === "elo"
            ? (b.placed ? 1e5 : 0) + num(b.elo_sort) - (a.placed ? 1e5 : 0) - num(a.elo_sort)
            : num(b[stat]) - num(a[stat]));
    const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
    page = Math.min(Math.max(page, 0), pages - 1);
    const slice = rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

    const embed = new EmbedBuilder()
        .setColor(BLUE)
        .setTitle(`🏅 Leaderboard: ${label}` + (period !== "all" ? ` · ${PERIODS[period]}` : ""));
    const url = pageUrl("/");
    if (url) embed.setURL(url);

    if (!slice.length) {
        embed.setDescription(`Nobody has ${min}+ matches${period !== "all" ? " in this period" : ""} yet.`);
    } else {
        const medal = ["🥇", "🥈", "🥉"];
        // every view shows rank + Elo; the third column is Rating 3.0 unless that's what we're ranking by
        const third = stat === "rating" || stat === "elo" ? null : "RTG";
        const lines = [`${"#".padStart(2)} ${cell("Player", 12)} ${head.padStart(6)}${third ? "  RTG" : ""} RANK ${stat === "elo" ? " AVG" : " ELO"}`];
        slice.forEach((p, i) => {
            const pos = page * PAGE_SIZE + i + 1;
            const main = stat === "elo" ? rt(p.rating) : String(fmt(p[stat]));
            lines.push(`${String(pos).padStart(2)} ${cell(p.name, 12)} ${(stat === "elo" ? eloShort(p) : main).padStart(6)}` +
                `${third ? " " + rt(p.rating).padStart(4) : ""} ${rankShort(p).padStart(4)} ${(stat === "elo" ? main : eloShort(p)).padStart(4)}`);
        });
        const top = rows[0];
        embed.setDescription(
            (page === 0 && !(stat === "elo" && !top.placed) ? `${medal[0]} **${top.name}** leads with ${fmt(top[stat])}\n` : "") +
            "```\n" + lines.join("\n") + "\n```"
        );
        embed.setFooter({
            text: `Page ${page + 1}/${pages} · ${rows.length} players with ${min}+ matches · ? = Elo hidden until placement matches are done` +
                (stat === "elo" ? " · AVG = average Rating 3.0" : "")
        });
    }

    const id = (p) => `lb:${p}:${stat}:${period}:${min}`;
    const nav = new ActionRowBuilder().addComponents(
        new ButtonBuilder().setCustomId(id(page - 1)).setLabel("◀ Prev").setStyle(ButtonStyle.Secondary).setDisabled(page === 0),
        new ButtonBuilder().setCustomId(id(page + 1)).setLabel("Next ▶").setStyle(ButtonStyle.Secondary).setDisabled(page >= pages - 1)
    );
    const site = linkButton("Full leaderboard", "/");
    if (site) nav.addComponents(site);
    return { embeds: [embed], components: [nav] };
}

// ---------------------------------------------------------------- match scorecard

function teamName(match, team) {
    return match[`team_${team.toLowerCase()}_name`] || `Team ${team}`;
}

// discord: { steamid: discordUserId } for players who ran /link (used to @mention them)
function matchEmbed({ match, players, highlights, odds }, discord = {}) {
    const a = match.score_a, b = match.score_b;
    const oddsLine = odds ? `\nPre-match odds: ${teamName(match, "A")} ${f0(100 * odds.win_prob_a)}% · ${f0(100 * odds.win_prob_b)}% ${teamName(match, "B")}` +
        (odds.upset ? " · **Upset!**" : "") : "";
    const embed = new EmbedBuilder()
        .setColor(a === b ? GOLD : GREEN)
        .setTitle(`${mapName(match.map)} · ${teamName(match, "A")} ${a} : ${b} ${teamName(match, "B")}`)
        .setDescription(`${when(match.played_at)} · ${match.rounds} rounds · match #${match.id}${oddsLine}`);
    const url = pageUrl(`/match/${match.id}`);
    if (url) embed.setURL(url);

    for (const [team, score, other] of [["A", a, b], ["B", b, a]]) {
        const rows = players.filter((p) => p.team === team).sort((x, y) => num(y.rating) - num(x.rating));
        if (!rows.length) continue;
        const lines = [`${cell("Player", 11)} ${"K-D-A".padStart(8)} ADR  RTG RANK  ELO`];
        for (const p of rows) {
            const r = Math.max(num(p.rounds), 1);
            lines.push(`${cell(p.name, 11)} ${`${p.kills}-${p.deaths}-${p.assists}`.padStart(8)} ${f0(p.damage / r).padStart(3)} ${rt(p.rating).padStart(4)} ` +
                `${rankShort(p).padStart(4)} ${(unplaced(p) ? "?" : signedInt(p.elo_delta)).padStart(4)}`);
        }
        embed.addFields({
            name: `${score > other ? "🏆 " : ""}${teamName(match, team)}: ${score}`,
            value: "```\n" + lines.join("\n") + "\n```"
        });
    }

    const who = (h) => `**${h.name}**` + (discord[h.steamid] ? ` (<@${discord[h.steamid]}>)` : "");
    if (highlights?.length)
        embed.addFields({ name: "Highlights", value: highlights.map((h) => `${h.icon} **${h.title}** ${who(h)}: ${h.text}`).join("\n").slice(0, 1024) });
    embed.setFooter({ text: `${match.source === "demo" ? "From the MatchZy demo" : "From the server log"} · RTG = Rating 3.0 (1.00 = group average) · RANK = current rank · ELO = points won/lost this match` });
    return embed;
}

function matchMessage(data, discord = {}) {
    return {
        embeds: [matchEmbed(data, discord)],
        components: rowOf(linkButton("Full scorecard", `/match/${data.match.id}`), linkButton("Leaderboard", "/"))
    };
}

// ---------------------------------------------------------------- player profile

function playerMessage({ player: p, history, group_size }) {
    const bar = (v) => "█".repeat(Math.round(num(v) / 10)).padEnd(10, "░");
    const embed = new EmbedBuilder()
        .setColor(BLUE)
        .setTitle(`📊 ${p.name}`)
        .setDescription(`**${rankText(p)}**\n` +
            (p.position ? `#${p.position} of ${group_size} on the leaderboard · ` : "") +
            `${p.matches} match${p.matches === 1 ? "" : "es"} · ${p.wins}W-${p.losses}L · ${p.rounds} rounds`);
    const url = pageUrl(`/player/${p.steamid}`);
    if (url) embed.setURL(url);
    embed.addFields(
        {
            name: "Overall",
            value: `Avg rating **${rt(p.rating)}** · Swing **${signed(p.swing_pr)}**/rd\n` +
                `K/D **${f2(p.kd)}** · ADR **${f1(p.adr)}** · KAST **${f0(p.kast)}%** · HS **${f0(p.hs_pct)}%**`
        },
        {
            name: "Profile (percentile in group)",
            value: "```\n" + ["firepower", "entrying", "trading", "clutching", "utility"]
                .map((k) => `${(k[0].toUpperCase() + k.slice(1)).padEnd(10)} ${bar(p[k])} ${f0(p[k]).padStart(3)}`).join("\n") + "\n```"
        },
        {
            name: "Duels",
            value: `Openings ${p.entry_wins}/${p.entry_attempts} (${f0(p.entry_win_pct)}%)\nTrade kills ${p.trade_kills}\nClutches ${p.clutch_wins}/${p.clutch_attempts} · Aces ${p.aces}`,
            inline: true
        },
        {
            name: "Utility",
            value: `Util dmg/rd ${f1(p.util_adr)}\nEnemies flashed ${p.enemies_flashed}\nTeammates flashed ${p.team_flashed}`,
            inline: true
        }
    );
    if (history?.length) {
        const lines = history.slice(0, 8).map((h) => {
            const [mine, theirs] = h.team === "A" ? [h.score_a, h.score_b] : [h.score_b, h.score_a];
            return `${h.result} ${cell(mapName(h.map), 8)} ${String(mine).padStart(2)}-${String(theirs).padEnd(2)} ${`${h.kills}-${h.deaths}`.padStart(5)} ${rt(h.rating).padStart(4)} ` +
                `${signedInt(h.elo_delta).padStart(4)} ${String(h.elo_after ?? "").padStart(4)}`;
        });
        embed.addFields({ name: "Recent matches (rating, Elo change, Elo after)", value: "```\n" + lines.join("\n") + "\n```" });
    }
    return { embeds: [embed], components: rowOf(linkButton("Full profile", `/player/${p.steamid}`)) };
}

module.exports = { STATS, PERIODS, leaderboardPage, matchMessage, playerMessage, mapName, when };
