import logging
import discord
from discord.ext import commands

import config


def setup_logging():
    """Sets up the root logger for the bot."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)-8s] %(name)-20s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    # Suppress overly verbose logs from discord.py's HTTP and gateway layers
    logging.getLogger('discord.http').setLevel(logging.WARNING)
    logging.getLogger('discord.gateway').setLevel(logging.WARNING)


async def log(bot: commands.Bot, message: str, embed: discord.Embed = None):
    """Sends a message or embed to the hard-coded log channel."""
    logger = logging.getLogger(__name__)
    if not str(config.LOG_CHANNEL_ID).isdigit() or config.LOG_CHANNEL_ID == 0:
        logger.warning("Log channel ID is not set correctly in config.py. Cannot send log message.")
        return

    try:
        # Use fetch_channel for reliability on startup
        channel = bot.get_channel(config.LOG_CHANNEL_ID) or await bot.fetch_channel(config.LOG_CHANNEL_ID)
        if channel:
            await channel.send(content=message, embed=embed)
        else:
            logger.warning(f"Could not find log channel with ID {config.LOG_CHANNEL_ID}. Was it deleted?")
    except (discord.Forbidden, discord.NotFound):
        logger.error(f"No permission or channel not found for log channel {config.LOG_CHANNEL_ID}.")
    except Exception as e:
        logger.exception(f"Failed to send log message to channel {config.LOG_CHANNEL_ID}: {e}")