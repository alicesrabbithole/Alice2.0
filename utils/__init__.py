# Empty File
# Make utils a package and export logging helpers for convenience.

from .discord_logging import setup_discord_logging, send_log
__all__ = ["setup_discord_logging", "send_log"]