// Discord posts for achievements, the weekly recap and season results.
const { AttachmentBuilder, EmbedBuilder } = require("discord.js");
const stats = require("./stats");
const { mapName } = require("./statsEmbeds");

const TIER_COLORS = { bronze: 0xc07a45, silver: 0xa9b3bd, gold: 0xf2c230, elite: 0xa66bff, shame: 0x7b8a7d };
const f2 = (v) => (v == null ? "–" : Number(v).toFixed(2));
const who = (p, discord = {}) => (discord[p.steamid] ? `<@${discord[p.steamid]}>` : `**${p.player || p.name}**`);

// Achievements unlocked in a match: one embed per unlock (badge as thumbnail), at most 10 per message
async function achievementMessages(unlocks, discord = {}) {
    if (!unlocks?.length) return [];
    const messages = [];
    for (let i = 0; i < unlocks.length; i += 10) {
        const chunk = unlocks.slice(i, i + 10);
        const files = new Map(), embeds = [];
        for (const a of chunk) {
            const png = await stats.badgePng(a.id);
            if (png && !files.has(a.id)) files.set(a.id, new AttachmentBuilder(png, { name: `${a.id}.png` }));
            const e = new EmbedBuilder()
                .setColor(TIER_COLORS[a.tier] || 0x3b82f6)
                .setTitle(a.tier === "shame" ? `Hall of shame: ${a.name}` : `Achievement unlocked: ${a.name}`)
                .setDescription(`${who(a, discord)} · ${a.description}`);
            if (png) e.setThumbnail(`attachment://${a.id}.png`);
            embeds.push(e);
        }
        messages.push({ embeds, files: [...files.values()], allowedMentions: { parse: [] } });
    }
    return messages;
}

function recapEmbed(rc) {
    const day = (iso) => `<t:${Math.floor(Date.parse(iso) / 1000)}:d>`;
    const e = new EmbedBuilder().setColor(0x8b5cf6).setTitle("Weekly recap")
        .setDescription(`${day(rc.since)} – ${day(rc.until)} · ${rc.matches} map${rc.matches === 1 ? "" : "s"}, ${rc.rounds} rounds, ${rc.players} players`);
    const url = stats.pageUrl("/community");
    if (url) e.setURL(url);
    const add = (name, value) => value && e.addFields({ name, value: value.slice(0, 1024), inline: true });
    add("Player of the week", rc.top_rating && `**${rc.top_rating.name}**\n${f2(rc.top_rating.rating)} avg rating · ${rc.top_rating.maps} maps`);
    add("Biggest climber", rc.top_elo && rc.top_elo.elo_change > 0 && `**${rc.top_elo.name}**\n+${rc.top_elo.elo_change} Elo`);
    add("Most kills", rc.top_kills && `**${rc.top_kills.name}**\n${rc.top_kills.kills} kills`);
    add("Best single game", rc.best_game && `**${rc.best_game.name}**\n${f2(rc.best_game.rating)} on ${mapName(rc.best_game.map)} (${rc.best_game.kills}-${rc.best_game.deaths})`);
    add("Biggest upset", rc.upset && `**${mapName(rc.upset.map)} ${rc.upset.score}**\nwon with a ${Math.round(100 * rc.upset.chance)}% chance`);
    add("Duo of the week", rc.best_duo && `**${rc.best_duo.players.map((p) => p.name).join(" + ")}**\n${rc.best_duo.wins}-${rc.best_duo.games - rc.best_duo.wins} together`);
    add("Clutch king", rc.most_clutches && `**${rc.most_clutches.name}**\n${rc.most_clutches.clutches} clutches`);
    add("Rivalry", rc.rivalry && `**${rc.rivalry.players.map((p) => p.name).join(" vs ")}**\n${rc.rivalry.players[0].kills}–${rc.rivalry.players[1].kills} in kills`);
    add("Aces", rc.most_aces && `**${rc.most_aces.name}**\n${rc.most_aces.aces} ace${rc.most_aces.aces > 1 ? "s" : ""}`);
    if (rc.achievements?.length) {
        const lines = rc.achievements.slice(0, 12).map((a) => `**${a.name}** · ${a.player}`);
        if (rc.achievements.length > 12) lines.push(`…and ${rc.achievements.length - 12} more`);
        e.addFields({ name: `New achievements (${rc.achievements.length})`, value: lines.join("\n").slice(0, 1024) });
    }
    return e;
}

async function seasonMessage(season) {
    const top = season.players.slice(0, 10);
    const medal = ["🥇", "🥈", "🥉"];
    const e = new EmbedBuilder().setColor(0xa66bff).setTitle(`${season.name} is over`)
        .setDescription(top.length
            ? top.map((p, i) => `${medal[i] || `**${i + 1}.**`} **${p.name}** · ${p.rank_short} ${p.elo}`).join("\n") +
              `\n\n**${top[0].name}** is Season Champion${top.length > 1 ? `; the top ${Math.min(3, top.length)} earn Podium` : ""}. ` +
              "Everyone's Elo moves halfway back to 1500 for the new season, and ranks show again after a couple of matches."
            : "Nobody finished placement this season.");
    const url = stats.pageUrl("/seasons");
    if (url) e.setURL(url);
    const png = await stats.badgePng("champion");
    if (png) e.setThumbnail("attachment://champion.png");
    return { embeds: [e], files: png ? [new AttachmentBuilder(png, { name: "champion.png" })] : [] };
}

module.exports = { achievementMessages, recapEmbed, seasonMessage };
