const { Client, GatewayIntentBits, Collection } = require("discord.js");
const fs = require("fs");
const config = require("./config.json");
const { startMatchFeed } = require("./lib/matchFeed");
const { startServerStatus } = require("./lib/serverStatus");
const { startSchedules } = require("./lib/schedules");
const mapVote = require("./lib/mapVote");

const client = new Client({
    intents: [
        GatewayIntentBits.Guilds,
        GatewayIntentBits.GuildMembers,
        GatewayIntentBits.GuildVoiceStates
    ]
});

client.commands = new Collection();
client.matches = new Map(); // store active matches

// Load command files dynamically
const commandFiles = fs.readdirSync("./commands").filter(file => file.endsWith(".js"));
for (const file of commandFiles) {
    const command = require(`./commands/${file}`);
    client.commands.set(command.data.name, command);
}

client.on("ready", () => {
    console.log(`Bot logged in as ${client.user.tag}`);
    startMatchFeed(client);   // posts CS2 scorecards when the stats service finishes a demo
    startServerStatus(client); // live server status message + "Watching de_mirage 8/10"
    startSchedules(client);    // weekly recap, season-end post, match night reminders
});

// Commands can own buttons by exporting buttonPrefix + handleButton (e.g. leaderboard paging)
const buttonHandlers = new Map();
for (const command of client.commands.values()) {
    if (command.buttonPrefix) buttonHandlers.set(command.buttonPrefix, command);
}

// Reply with an error whether or not the interaction was already acknowledged
async function replyError(interaction, err) {
    const content = err && err.name === "StatsError" ? err.message : "Error running command";
    try {
        if (interaction.deferred || interaction.replied) await interaction.followUp({ content, ephemeral: true });
        else await interaction.reply({ content, ephemeral: true });
    } catch (_) {}
}

// Slash command + button interactions
client.on("interactionCreate", async (interaction) => {
    if (interaction.isChatInputCommand()) {
        const command = client.commands.get(interaction.commandName);
        if (!command) return;
        try {
            await command.execute(interaction, client);
        } catch (err) {
            console.error(err);
            await replyError(interaction, err);
        }
    }

    // Autocomplete (e.g. player names in /stats)
    if (interaction.isAutocomplete()) {
        const command = client.commands.get(interaction.commandName);
        if (command && command.autocomplete) {
            try {
                await command.autocomplete(interaction);
            } catch (err) {
                console.error(err);
            }
        }
        return;
    }

    // Map vote: the setup menu and the "type a map" form
    if (interaction.isStringSelectMenu() || interaction.isModalSubmit()) {
        const [action, matchId] = interaction.customId.split(":");
        const match = client.matches.get(matchId);
        if (!match) return interaction.reply({ content: "Match no longer exists.", ephemeral: true }).catch(() => {});
        try {
            await mapVote.handle(interaction, action, match);
        } catch (err) {
            console.error(err);
            await replyError(interaction, err);
        }
        return;
    }

    // "Choose Players" member picker in a lobby
    if (interaction.isUserSelectMenu()) {
        const [action, matchId] = interaction.customId.split(":");
        const match = client.matches.get(matchId);
        if (action === "setplayers") {
            if (!match) return interaction.reply({ content: "Match no longer exists.", ephemeral: true });
            try {
                await match.setPlayers(interaction);
            } catch (err) {
                console.error(err);
                await replyError(interaction, err);
            }
        }
        return;
    }

    // BUTTON INTERACTIONS
    if (interaction.isButton()) {
        const owner = buttonHandlers.get(interaction.customId.split(":")[0]);
        if (owner) {
            try {
                await owner.handleButton(interaction, client);
            } catch (err) {
                console.error(err);
                await replyError(interaction, err);
            }
            return;
        }

        const [action, matchId] = interaction.customId.split(":");

        const match = client.matches.get(matchId);
        if (!match)
            return interaction.reply({ content: "This lobby is gone (the bot restarted). Run /startmatch again.", ephemeral: true }).catch(() => {});

        // Any error in a lobby button is logged and reported, never allowed to take the bot down
        try {
            await handleMatchButton(interaction, action, match);
        } catch (err) {
            console.error(`[button ${action}]`, err);
            await replyError(interaction, err);
        }
    }
});

async function handleMatchButton(interaction, action, match) {
    {

        if (action === "join") {
            if (match.players.includes(interaction.user.id))
                return interaction.reply({ content: "You already joined.", ephemeral: true });

            if (match.players.length >= 20)
                return interaction.reply({ content: "This match is full.", ephemeral: true });

            match.players.push(interaction.user.id);
            await match.updateLobbyMessage(interaction);
        }

        if (action === "start_picking") {
            if (interaction.user.id !== match.adminId)
                return interaction.reply({ content: "Only the admin can start picking.", ephemeral: true });

            await match.beginCaptainSelection(interaction);
        }

        if (action.startsWith("choosecap")) {
            const captainId = action.split("-")[1];

            if (!match.captains.includes(captainId))
                match.captains.push(captainId);

            if (match.captains.length === 2) {
                await match.startDraft(interaction);
            } else {
                await interaction.reply({ content: "Captain 1 selected. Choose the second captain.", ephemeral: true });
            }
        }

        if (action.startsWith("pick")) {
            const pickId = action.split("-")[1];

            await match.pickPlayer(interaction, pickId);
        }

        if (action === "move_players") {
            await match.movePlayers(interaction, client);
        }

        if (action === "randomize") {
            await match.randomizeTeams(interaction);
        }

        if (action === "addvoice") {
            await match.addVoicePlayers(interaction);
        }

        if (action === "chooseplayers") {
            await match.choosePlayers(interaction);
        }

        if (action === "tolobby") {
            await match.backToLobby(interaction);
        }

        if (action.startsWith("map")) {
            await mapVote.handle(interaction, action, match);
        }
    }
}

// Last line of defense: log anything unexpected instead of crashing (a crash loses every open lobby)
client.on("error", (err) => console.error("[discord]", err));
process.on("unhandledRejection", (err) => console.error("[unhandled]", err));
process.on("uncaughtException", (err) => console.error("[uncaught]", err));

client.login(process.env.DISCORD_TOKEN);
