import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
import logging

import config
from utils.db_utils import load_data
from utils.log_utils import setup_logging


# --- Setup ---
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
IS_REPLIT = os.getenv("REPL_ID") is not None

setup_logging()
logger = logging.getLogger(__name__)

# --- Intents and Bot Class ---
intents = discord.Intents.default()
intents.message_content = True


class AliceBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix="!",
            intents=intents,
            owner_id=config.OWNER_ID,
            help_command=None  # We use our custom help command
        )
        self.data = load_data()
        self.initial_extensions = [
            "cogs.admin_cog",
            "cogs.help_cog",
            "cogs.moderation_cog",
            "cogs.puzzle_drops_cog",
            "cogs.puzzles_cog",
            "cogs.role_utility_cog",
            "sticky_cog"
        ]

    async def setup_hook(self):
        """This is called when the bot is loading its extensions."""
        logger.info("--- Loading Cogs ---")
        for extension in self.initial_extensions:
            try:
                await self.load_extension(extension)
                logger.info(f"Successfully loaded extension: {extension}")
            except Exception as e:
                logger.exception(f"Failed to load extension {extension}.")

    async def on_ready(self):
        logger.info(f'--- Logged in as {self.user} (ID: {self.user.id}) ---')
        logger.info('Bot is ready and online.')
        logger.info("Use !reload or !sync to update application commands if needed.")


# --- Bot Initialization and Run ---
bot = AliceBot()

if __name__ == "__main__":
    if TOKEN is None:
        logger.critical("DISCORD_TOKEN environment variable not found. Please set it in your .env file.")
    elif config.OWNER_ID == 0:
        logger.critical("OWNER_ID has not been set in config.py. Please set it to your Discord User ID.")
    else:
        bot.run(TOKEN)