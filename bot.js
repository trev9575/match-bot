const { Client, GatewayIntentBits, Collection } = require("discord.js");
const fs = require("fs");
const config = require("./config.json");
const { startMatchFeed } = require("./lib/matchFeed");

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
            return interaction.reply({ content: "Match no longer exists.", ephemeral: true });

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
    }
});

client.login(process.env.DISCORD_TOKEN);
