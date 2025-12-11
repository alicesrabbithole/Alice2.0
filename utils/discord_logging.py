import logging
import discord
from discord.ext import commands
from typing import Optional

logger = logging.getLogger(__name__)

def setup_discord_logging():
    """Set quieter logging for noisy libraries. Call after root logging is configured."""
    logging.getLogger("discord.http").setLevel(logging.WARNING)
    logging.getLogger("discord.gateway").setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.INFO)
    logging.getLogger("aiohttp").setLevel(logging.INFO)

async def send_log(bot: commands.Bot, message: str, embed: Optional[discord.Embed] = None):
    """
    Send a message or embed to the configured log channel.
    Expects config.LOG_CHANNEL_ID to be set (int or numeric string).
    This function is tolerant: logs locally on failure and never raises.
    """
    try:
        import config  # local import so module-level import won't fail if config is missing earlier
    except Exception:
        logger.warning("config module not available; cannot send discord log")
        return

    if not getattr(config, "LOG_CHANNEL_ID", None):
        logger.debug("LOG_CHANNEL_ID not set; skipping send_log()")
        return

    try:
        chan_id = int(config.LOG_CHANNEL_ID)
    except Exception:
        logger.debug("LOG_CHANNEL_ID is not an integer; skipping send_log()")
        return

    try:
        ch = bot.get_channel(chan_id) or await bot.fetch_channel(chan_id)
        if ch:
            await ch.send(content=message, embed=embed)
        else:
            logger.warning("send_log: could not resolve channel id %s", chan_id)
    except (discord.Forbidden, discord.NotFound):
        logger.exception("send_log: no permission or channel not found for %s", chan_id)
    except Exception:
        logger.exception("send_log: unexpected error while sending to channel %s", chan_id)