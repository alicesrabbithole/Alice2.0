import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
import logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)8s] %(name)20s : %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
import config
from utils.db_utils import load_data
from utils.log_utils import setup_discord_logging

# --- Setup ---
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

setup_discord_logging()
logger = logging.getLogger(__name__)

# --- Intents and Bot Class ---
intents = discord.Intents.default()
intents.message_content = True  # Make sure this is enabled in the Developer Portal


class AliceBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix="!",
            intents=intents,
            owner_id=config.OWNER_ID,
            help_command=None  # We use our custom help command
        )
        self.data = load_data()
        logger.info(f"[DEBUG] Loaded data keys: {list(self.data.keys())}")
        logger.info(
            f"[DEBUG] Sample user_pieces[1077240270791397388]: {self.data.get('user_pieces', {}).get('1077240270791397388', {})}")
        self.initial_extensions = [
            "cogs.admin_cog",
            "cogs.afk_cog",
            "cogs.alice_help_cog",
            "cogs.giveaway_cog",
            "cogs.global_message_leaderboard_cog",
            "cogs.moderation_cog",
            "cogs.puzzle_drops_cog",
            "cogs.puzzles_cog",
            "cogs.reminder_cog",
            "cogs.role_utility_cog",
            "cogs.sticky_cog",
            "cogs.copy_category_cog",
            "games.wordle_cog",
            "games.twentyone_questions_cog",
            "games.rolling_cog",
            "cogs.rumble_admin_cog",
            "cogs.rumble_listener_cog",
            "cogs.stocking_cog",
            "cogs.channel_alias_cog",
            "cogs.usage_logger_cog"
        ]

    # --- THIS IS THE FIX ---
    # This setup_hook is now much simpler and more reliable.
    # It loads all the code first, and then syncs everything exactly one time.
    # This will prevent the bot from getting stuck.
    async def setup_hook(self):
        """This is called once when the bot logs in."""
        logger.info("--- Running Setup Hook ---")

        # 1. Load all cogs
        logger.info("--- Loading Cogs ---")
        for extension in self.initial_extensions:
            try:
                await self.load_extension(extension)
                logger.info(f"Successfully loaded extension: {extension}")
            except Exception as e:
                logger.exception(f"Failed to load extension {extension}.")

        # 2. Sync the commands exactly once.
        if config.GUILD_ID:
            logger.info("Syncing commands to guild...")
            self.tree.copy_global_to(guild=discord.Object(id=config.GUILD_ID))
            await self.tree.sync(guild=discord.Object(id=config.GUILD_ID))
            logger.info("Commands synced to guild.")
        else:
            logger.info("Syncing commands globally...")
            await self.tree.sync()
            logger.info("Commands synced globally.")

    async def on_ready(self):
        """Called when the bot is ready and online."""
        logger.info(f'--- Logged in as {self.user} (ID: {self.user.id}) ---')
        logger.info('Bot is ready and online.')

    async def on_message(self, message: discord.Message):
        """
        This event is called for every message the bot sees.
        We need this to ensure prefix commands are processed.
        """
        if message.author.bot:
            return
        await self.process_commands(message)

    async def on_command(self, ctx: commands.Context):
        """This event is triggered every time a command is successfully invoked."""
        if ctx.command is None:
            return
        logger.info(
            f"COMMAND: User '{ctx.author}' (ID: {ctx.author.id}) ran command '{ctx.command.name}' "
            f"in channel '{ctx.channel}' (ID: {ctx.channel.id})"
        )


# --- Bot Initialization and Run ---
bot = AliceBot()

if __name__ == "__main__":
    if TOKEN is None:
        logger.critical("DISCORD_TOKEN environment variable not found. Please set it in your .env file.")
    elif config.OWNER_ID == 0:
        logger.critical("OWNER_ID has not been set in config.py. Please set it to your Discord User ID.")
    else:
        bot.run(TOKEN)