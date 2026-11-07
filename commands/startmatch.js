const { SlashCommandBuilder, ActionRowBuilder, ButtonBuilder, ButtonStyle, EmbedBuilder, UserSelectMenuBuilder } = require("discord.js");
const { buildConnectMessage } = require("../lib/serverInfo");
const teams = require("../lib/teams");
const { predictionEmbed } = require("../lib/prediction");
const { lobbyChannelId, moveTeamsToLobby } = require("../lib/voice");

const MAX_PLAYERS = 20;

// Lobby buttons: anyone can join; the second row is for whoever started the match
function lobbyComponents(matchId) {
    return [
        new ActionRowBuilder().addComponents(
            new ButtonBuilder()
                .setCustomId(`join:${matchId}`)
                .setLabel("Participate in Match")
                .setStyle(ButtonStyle.Success)
        ),
        new ActionRowBuilder().addComponents(
            new ButtonBuilder()
                .setCustomId(`start_picking:${matchId}`)
                .setLabel("Start Picking")
                .setStyle(ButtonStyle.Primary),
            new ButtonBuilder()
                .setCustomId(`randomize:${matchId}`)
                .setLabel("Randomize Teams (by Elo)")
                .setStyle(ButtonStyle.Secondary),
            new ButtonBuilder()
                .setCustomId(`addvoice:${matchId}`)
                .setLabel("Add Everyone in Voice")
                .setStyle(ButtonStyle.Secondary),
            new ButtonBuilder()
                .setCustomId(`chooseplayers:${matchId}`)
                .setLabel("Choose Players")
                .setStyle(ButtonStyle.Secondary),
            new ButtonBuilder()
                .setCustomId(`mapvote:${matchId}`)
                .setLabel("Map Vote")
                .setStyle(ButtonStyle.Secondary)
        )
    ];
}

function lobbyEmbed(matchId, players) {
    return new EmbedBuilder()
        .setTitle(`Match Lobby: ${matchId}`)
        .setDescription(
            players.length === 0
                ? "No players have joined yet."
                : players.map(id => `<@${id}>`).join("\n")
        )
        .setFooter({ text: `${players.length} player${players.length === 1 ? "" : "s"} · the second row of buttons is for whoever started the match` })
        .setColor("Blue");
}

module.exports = {
    data: new SlashCommandBuilder()
        .setName("startmatch")
        .setDescription("Start a new match lobby")
        .addStringOption(opt =>
            opt.setName("id")
               .setDescription("Optional match ID")
               .setRequired(false)
        ),

    async execute(interaction, client) {
        const matchId = interaction.options.getString("id") || Date.now().toString();

        if (client.matches.has(matchId))
            return interaction.reply({ content: "That match ID already exists.", ephemeral: true });

        await openLobby(client, {
            matchId,
            adminId: interaction.user.id,
            lobbyVoiceId: interaction.member?.voice?.channelId,
            send: async (payload) => { await interaction.reply(payload); return interaction.fetchReply(); }
        });
    },
    openLobby
};

// Creates a lobby and posts it with send(payload) -> Message. Used by /startmatch and match nights.
async function openLobby(client, { matchId = Date.now().toString(), adminId, players = [], lobbyVoiceId = null, send, intro = null }) {
        const match = {
            id: matchId,
            adminId,
            lobbyVoiceId,
            players: players.slice(0, MAX_PLAYERS),
            captains: [],
            team1: [],
            team2: [],
            turn: null,

            // Update the original lobby message (used when someone clicks "Participate")
            async updateLobbyMessage(interaction) {
                const embed = lobbyEmbed(matchId, match.players);
                const components = lobbyComponents(matchId);

                // This expects to be called from the original lobby message interaction (button)
                // Use update so the original message gets edited
                try {
                    await interaction.update({ embeds: [embed], components });
                } catch (err) {
                    // If we can't update (maybe interaction already acknowledged), fallback to channel edit/send
                    try {
                        await interaction.channel.send({ embeds: [embed], components });
                    } catch (_) {}
                }
            },

            // Edit the lobby message from somewhere else (e.g. the private "Choose Players" picker)
            async refreshLobby() {
                if (!match.lobbyMessage) return;
                try {
                    await match.lobbyMessage.edit({ embeds: [lobbyEmbed(matchId, match.players)], components: lobbyComponents(matchId) });
                } catch (_) {}
            },

            // "Add Everyone in Voice": the starter's voice channel plus both team channels
            async addVoicePlayers(interaction) {
                if (!teams.canRun(interaction, match))
                    return interaction.reply({ content: "Only the person who started the match can add players.", ephemeral: true });
                const cfg = require("../config.json");
                const ids = [interaction.member?.voice?.channelId, cfg.team1VoiceChannelId, cfg.team2VoiceChannelId];
                const found = [];
                for (const id of new Set(ids.filter(Boolean))) {
                    const channel = interaction.guild.channels.cache.get(id);
                    if (!channel || !channel.members) continue;
                    for (const m of channel.members.values()) if (!m.user.bot) found.push(m.id);
                }
                const added = [], full = [];
                for (const id of found) {
                    if (match.players.includes(id)) continue;
                    if (match.players.length >= MAX_PLAYERS) { full.push(id); continue; }
                    match.players.push(id);
                    added.push(id);
                }
                await match.updateLobbyMessage(interaction);
                const msg = !found.length
                    ? "Nobody is in voice. Join a voice channel first (or use the team channels), or use **Choose Players**."
                    : (added.length ? `Added ${added.length}: ${added.map(id => `<@${id}>`).join(", ")}.` : "Everyone in voice was already in.") +
                      (full.length ? ` The lobby is full (${MAX_PLAYERS}), so ${full.map(id => `<@${id}>`).join(", ")} couldn't be added.` : "");
                try { await interaction.followUp({ content: msg, ephemeral: true }); } catch (_) {}
            },

            // "Choose Players": a private member picker, pre-filled with who's already in
            async choosePlayers(interaction) {
                if (!teams.canRun(interaction, match))
                    return interaction.reply({ content: "Only the person who started the match can choose players.", ephemeral: true });
                const picker = new UserSelectMenuBuilder()
                    .setCustomId(`setplayers:${matchId}`)
                    .setPlaceholder("Pick everyone who's playing")
                    .setMinValues(0)
                    .setMaxValues(MAX_PLAYERS);
                if (match.players.length) picker.setDefaultUsers(match.players.slice(0, MAX_PLAYERS));
                await interaction.reply({
                    content: "Pick everyone who's playing. People already in the lobby are ticked; untick someone to take them out.",
                    components: [new ActionRowBuilder().addComponents(picker)],
                    ephemeral: true
                });
            },

            async setPlayers(interaction) {
                if (!teams.canRun(interaction, match))
                    return interaction.reply({ content: "Only the person who started the match can choose players.", ephemeral: true });
                const picked = interaction.values.filter(id => !interaction.users.get(id)?.bot);
                match.players = picked.slice(0, MAX_PLAYERS);
                await interaction.update({ content: `Lobby updated: ${match.players.length} player${match.players.length === 1 ? "" : "s"}.`, components: [] });
                await match.refreshLobby();
            },

            // Begin selecting captains - invoked by admin clicking "Start Picking"
            async beginCaptainSelection(interaction) {
                // This is called by a button interaction — acknowledge quickly
                await interaction.deferUpdate();

                if (match.players.length < 2)
                    return interaction.channel.send({ content: "Need at least 2 players to select captains." });

                const embed = new EmbedBuilder()
                    .setTitle("Select 2 Captains")
                    .setDescription("Click a player to select them as captain.");

                // Build rows of up to 5 buttons each
                const rows = [];
                let currentRow = new ActionRowBuilder();

                match.players.forEach(id => {
                    // If current row has 5 buttons, push it and start a new row
                    if (currentRow.components.length === 5) {
                        rows.push(currentRow);
                        currentRow = new ActionRowBuilder();
                    }

                    currentRow.addComponents(
                        new ButtonBuilder()
                            .setCustomId(`choosecap-${id}:${matchId}`)
                            .setLabel(interaction.guild.members.cache.get(id)?.displayName || `<@${id}>`)
                            .setStyle(ButtonStyle.Secondary)
                    );
                });

                if (currentRow.components.length > 0) rows.push(currentRow);

                await interaction.channel.send({ embeds: [embed], components: rows });
            },

            // After 2 captains chosen, start draft — assign captains into teams here
            async startDraft(interaction) {
                // Called from a button interaction — ack quickly
                await interaction.deferUpdate();

                // Ensure we have two captains
                if (match.captains.length < 2) {
                    return interaction.channel.send("Need two captains to start the draft.");
                }

                // Put captains into each team immediately
                match.team1 = [match.captains[0]];
                match.team2 = [match.captains[1]];

                // Decide who picks first
                match.turn = Math.random() < 0.5 ? match.captains[0] : match.captains[1];

                await interaction.channel.send({
                    content: `Draft is starting! First pick: <@${match.turn}>`,
                });

                await match.showDraftBoard(interaction);
            },

            // Draw the draft board with pick buttons for available players
            async showDraftBoard(interaction) {
                // Build embed
                const embed = new EmbedBuilder()
                    .setTitle("Team Draft")
                    .addFields(
                        { name: "Captain 1", value: `<@${match.captains[0] || "TBD"}>`, inline: true },
                        { name: "Captain 2", value: `<@${match.captains[1] || "TBD"}>`, inline: true },
                        { name: "Team 1", value: match.team1.length > 0 ? match.team1.map(id => `<@${id}>`).join("\n") : "None", inline: true },
                        { name: "Team 2", value: match.team2.length > 0 ? match.team2.map(id => `<@${id}>`).join("\n") : "None", inline: true }
                    );

                const available = match.players.filter(
                    p => !match.captains.includes(p) && !match.team1.includes(p) && !match.team2.includes(p)
                );

                // Build pick buttons into rows (up to 5 per row)
                const rows = [];
                let currentRow = new ActionRowBuilder();

                available.forEach(id => {
                    if (currentRow.components.length === 5) {
                        rows.push(currentRow);
                        currentRow = new ActionRowBuilder();
                    }

                    currentRow.addComponents(
                        new ButtonBuilder()
                            .setCustomId(`pick-${id}:${matchId}`)
                            .setLabel(interaction.guild.members.cache.get(id)?.displayName || `<@${id}>`)
                            .setStyle(ButtonStyle.Secondary)
                    );
                });

                if (currentRow.components.length > 0) rows.push(currentRow);

                // If no picks left, show the server connect info and a "Move Players" button for admin
                const embeds = [embed];
                if (available.length === 0) {
                    // Join CS2 info (if a server is configured)
                    const connect = buildConnectMessage();
                    if (connect) {
                        embeds.push(connect.embed);
                        if (connect.row) rows.push(connect.row);
                    }

                    // Add a control row so admin can manually move players (optional)
                    const controlRow = new ActionRowBuilder().addComponents(
                        new ButtonBuilder()
                            .setCustomId(`move_players:${matchId}`)
                            .setLabel("Move Players to Voice")
                            .setStyle(ButtonStyle.Primary),
                        new ButtonBuilder()
                            .setCustomId(`tolobby:${matchId}`)
                            .setLabel("Back to Lobby")
                            .setStyle(ButtonStyle.Secondary)
                    );
                    if (match.map) embed.addFields({ name: "Map", value: require("../lib/mapVote").pretty(match.map) });
                    rows.push(controlRow);
                }

                // If rows is empty (no buttons at all), send just the embed; otherwise send embed + components
                if (rows.length === 0) {
                    await interaction.channel.send({ embeds });
                } else {
                    await interaction.channel.send({ embeds, components: rows });
                }
            },

            // Handle picking a player (button interaction)
            async pickPlayer(interaction, pickId) {
                // Acknowledge the button interaction immediately
                await interaction.deferUpdate();

                // Is it the correct player's turn?
                if (interaction.user.id !== match.turn) {
                    return interaction.followUp({ content: "Not your turn.", ephemeral: true });
                }

                // Validate pickId - ensure not a captain and not already drafted
                if (match.captains.includes(pickId) || match.team1.includes(pickId) || match.team2.includes(pickId)) {
                    return interaction.followUp({ content: "That player has already been picked.", ephemeral: true });
                }

                // Add to the appropriate team
                if (match.captains[0] === match.turn) {
                    match.team1.push(pickId);
                } else {
                    match.team2.push(pickId);
                }

                // Switch turn
                match.turn = match.turn === match.captains[0] ? match.captains[1] : match.captains[0];

                // Check remaining available players
                const available = match.players.filter(
                    p => !match.captains.includes(p) && !match.team1.includes(p) && !match.team2.includes(p)
                );

                // If draft finished, move players into voice channels automatically
                if (available.length === 0) {
                    await match.showDraftBoard(interaction); // show final team board with connect button
                    await match.postPrediction(interaction.channel, interaction.guild);
                    await match.movePlayers(interaction, client);
                    return;
                }

                // Otherwise continue the draft
                await match.showDraftBoard(interaction);
            },

            // "Randomize Teams": two random captains, everyone else split so average Elo is even
            async randomizeTeams(interaction) {
                if (!teams.canRun(interaction, match))
                    return interaction.reply({ content: "Only the person who started the match can randomize teams.", ephemeral: true });
                if (match.players.length < 2)
                    return interaction.reply({ content: "Need at least 2 players to make teams.", ephemeral: true });
                await interaction.deferUpdate();

                const result = await teams.balancedTeams(match.players);
                match.captains = result.captains;
                match.team1 = result.team1;
                match.team2 = result.team2;
                match.turn = null;

                await interaction.channel.send({ embeds: [teams.balanceEmbed(result, interaction.guild)] });
                await match.showDraftBoard(interaction);   // final board: connect info + move button
                await match.postPrediction(interaction.channel, interaction.guild);
                await match.movePlayers(interaction, client);
            },

            // Win odds and each player's Elo at stake, once teams are set
            async postPrediction(channel, guild) {
                const name = (id) => `Team ${guild?.members.cache.get(id)?.displayName || "?"}`;
                const e = await predictionEmbed(match.team1, match.team2, name(match.captains[0]), name(match.captains[1]));
                if (e) { try { await channel.send({ embeds: [e] }); } catch (_) {} }
            },

            // "Back to Lobby": everyone in the team channels goes back to the lobby voice channel
            async backToLobby(interaction) {
                if (!teams.canRun(interaction, match) && !match.team1.concat(match.team2).includes(interaction.user.id))
                    return interaction.reply({ content: "Only players in this match can do that.", ephemeral: true });
                const lobby = lobbyChannelId(match.lobbyVoiceId);
                if (!lobby)
                    return interaction.reply({ content: "No lobby voice channel set (LOBBY_VOICE_CHANNEL_ID), and the match starter wasn't in voice.", ephemeral: true });
                await interaction.deferReply();
                const n = await moveTeamsToLobby(interaction.guild, lobby);
                await interaction.editReply(n ? `Moved ${n} player${n === 1 ? "" : "s"} back to <#${lobby}>.` : "Nobody was in the team channels.");
            },

            // Move users into configured voice channels
            async movePlayers(interaction, client) {
                // acknowedge the button if called directly
                try { await interaction.deferUpdate(); } catch (_) {}

                const cfg = require("../config.json");
                const t1 = cfg.team1VoiceChannelId;
                const t2 = cfg.team2VoiceChannelId;

                // Ensure channels exist
                const guild = interaction.guild;
                if (!guild) return;

                // Move team1
                for (const id of match.team1) {
                    const m = guild.members.cache.get(id) || await guild.members.fetch(id).catch(() => null);
                    if (m && m.voice) {
                        try {
                            await m.voice.setChannel(t1);
                        } catch (_) {}
                    } else if (m) {
                        // user not in voice — try to move anyway, setChannel will place them if possible
                        try { await m.voice.setChannel(t1).catch(() => {}); } catch(_) {}
                    }
                }

                // Move team2
                for (const id of match.team2) {
                    const m = guild.members.cache.get(id) || await guild.members.fetch(id).catch(() => null);
                    if (m && m.voice) {
                        try {
                            await m.voice.setChannel(t2);
                        } catch (_) {}
                    } else if (m) {
                        try { await m.voice.setChannel(t2).catch(() => {}); } catch(_) {}
                    }
                }

                // Inform the channel
                try {
                    await interaction.followUp({ content: "Players moved to voice channels.", ephemeral: false });
                } catch (err) {
                    // fallback to channel message
                    try { await interaction.channel.send("Players moved to voice channels."); } catch(_) {}
                }
            },
        };

        client.matches.set(matchId, match);

        // Send initial lobby message (kept so the private player picker can edit it later)
        const embed = match.players.length ? lobbyEmbed(matchId, match.players) : new EmbedBuilder()
            .setTitle(`Match Lobby: ${matchId}`)
            .setDescription("Click below to join.")
            .setColor(0x00AEFF);

        try {
            match.lobbyMessage = await send({ content: intro || undefined, embeds: [embed], components: lobbyComponents(matchId) });
        } catch (err) {
            client.matches.delete(matchId);
            throw err;
        }
        return match;
}
