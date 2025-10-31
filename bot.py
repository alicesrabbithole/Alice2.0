import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
import logging

from cogs.utils.db_utils import load_data
from cogs.utils.log_utils import setup_logging
from keep_alive import keep_alive

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
setup_logging()
logger = logging.getLogger(__name__)

# Make sure your User ID is set here!
OWNER_ID = 278345224973385728


class AliceBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True

        super().__init__(command_prefix="!", intents=intents, owner_id=OWNER_ID, help_command=None)

        self.data = load_data()
        self.initial_extensions = [
            "cogs.puzzle_drops_cog",
            "cogs.puzzles_cog",
            "cogs.admin_cog",
            "cogs.help_cog"
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

        # --- THIS IS THE FIX ---
        # The command sync has been removed from here to prevent rate limiting on startup.
        # Use !reload or !sync to update commands manually.

    async def on_ready(self):
        logger.info(f'--- Logged in as {self.user} (ID: {self.user.id}) ---')
        logger.info('Bot is ready and online.')
        logger.info("Use !reload or !sync to update application commands if needed.")


bot = AliceBot()
keep_alive()

if __name__ == "__main__":
    if TOKEN is None:
        logger.critical("DISCORD_TOKEN environment variable not found. Please set it in your .env file.")
    elif str(OWNER_ID) == "YOUR_USER_ID_HERE":
        logger.critical("OWNER_ID has not been set in bot.py. Please set it to your Discord User ID.")
    else:
        bot.run(TOKEN)