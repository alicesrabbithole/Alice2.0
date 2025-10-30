import logging
import discord
from discord.ext import commands

# --- HARD-CODED SETTINGS ---
# Replace this with the actual ID of your logging channel.
# Right-click the channel in Discord and select "Copy Channel ID".
LOG_CHANNEL_ID = "1411859714144468992"


# ---

def setup_logging():
    """Sets up the root logger."""
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)-8s] %(name)-15s: %(message)s')
    # Suppress overly verbose logs from discord.py
    logging.getLogger('discord.http').setLevel(logging.WARNING)
    logging.getLogger('discord.gateway').setLevel(logging.WARNING)


async def log(bot: commands.Bot, message: str):
    """Sends a message to the hard-coded log channel."""
    logger = logging.getLogger(__name__)
    if not LOG_CHANNEL_ID.isdigit():
        logger.warning("Log channel ID is not set. Cannot send log message.")
        return

    try:
        channel = bot.get_channel(int(LOG_CHANNEL_ID))
        if channel:
            await channel.send(message)
        else:
            # This can happen if the bot hasn't fully loaded its channel cache yet.
            logger.warning(f"Could not find log channel with ID {LOG_CHANNEL_ID}. Was it deleted?")
    except Exception as e:
        logger.exception(f"Failed to send log message to channel {LOG_CHANNEL_ID}: {e}")