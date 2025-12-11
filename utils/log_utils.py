# Compatibility shim: re-export the public helpers from utils.discord_logging
# and provide a `log` logger object for callers that import it.
# You can remove this file after you've updated all call sites to import from utils.discord_logging.

import warnings
import logging
from typing import Optional
import discord
from discord.ext import commands

# Provide a module-level logger named `log` for compatibility with existing imports:
log = logging.getLogger("alice.log_utils")

try:
    # Prefer the new canonical module
    from .discord_logging import send_log as _send_log, setup_discord_logging as _setup_discord_logging  # type: ignore
except Exception:
    # Provide simple fallbacks to avoid import-time crashes
    _send_log = None  # type: ignore
    _setup_discord_logging = None  # type: ignore

def setup_discord_logging():
    """Compatibility wrapper."""
    warnings.warn("utils.log_utils is deprecated; import from utils.discord_logging instead", DeprecationWarning, stacklevel=2)
    if _setup_discord_logging:
        return _setup_discord_logging()
    return None

async def send_log(bot: commands.Bot, message: str, embed: Optional[discord.Embed] = None):
    """Compatibility wrapper."""
    warnings.warn("utils.log_utils.send_log is deprecated; import from utils.discord_logging instead", DeprecationWarning, stacklevel=2)
    if _send_log:
        return await _send_log(bot, message, embed)  # type: ignore
    # no-op fallback
    return None

__all__ = ["setup_discord_logging", "send_log", "log"]