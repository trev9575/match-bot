FROM node:20-slim

WORKDIR /app

# Install dependencies first (better layer caching)
COPY package*.json ./
RUN npm ci --omit=dev

# Copy the bot code
COPY . .

# Register slash commands on every start, then run the bot
# (exec so the bot gets stop signals from Docker directly)
CMD ["sh", "-c", "node deploy-commands.js; exec node bot.js"]
