#!/usr/bin/env python3
"""
Updated RumbleListenerCog:
- Improved winner detection regex.
- Combined message and embed text for better parsing.
- Added checks before cog reloading.
"""

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)

DATA_DIR = Path("data")
CONFIG_FILE = DATA_DIR / "rumble_listener_config.json"

WINNER_TITLE_RE = re.compile(r"(?i)\b(?:winner|win|won|reward|prize|__winner__|:crwn2:|received)\b", re.IGNORECASE)


def ensure_data_dir():
    """Ensure data directory exists."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)


class RumbleListenerCog(commands.Cog):
    def __init__(self, bot: commands.Bot, initial_config: Optional[Dict[str, List]] = None):
        self.bot = bot
        self.rumble_bot_ids: List[int] = []
        self.channel_part_map: Dict[int, Tuple[str, str]] = {}
        ensure_data_dir()
        if initial_config:
            self._load_config_from_dict(initial_config)
        self._load_config_file()
        logger.info("RumbleListener initialized with bot IDs=%r and channel mappings=%r", self.rumble_bot_ids, self.channel_part_map)

    def _load_config_from_dict(self, data: Dict[str, List]) -> None:
        """Load configuration from dict."""
        self.rumble_bot_ids = list(map(int, data.get("rumble_bot_ids", [])))
        self.channel_part_map = {int(ch_id): tuple(map(str, val)) for ch_id, val in data.get("channel_part_map", {}).items()}

    def _load_config_file(self) -> None:
        """Load configuration file."""
        try:
            if CONFIG_FILE.exists():
                with CONFIG_FILE.open("r", encoding="utf-8") as fh:
                    config_data = json.load(fh)
                    self._load_config_from_dict(config_data)
                logger.info(f"Config file loaded: {config_data}")
        except Exception as e:
            logger.exception(f"Failed to load config file: {str(e)}")

    async def _handle_awards(self, winner_ids: List[int], channel_id: int) -> None:
        """Award parts to winners."""
        mapping = self.channel_part_map.get(channel_id)
        if not mapping:
            logger.warning(f"No mapping found for channel ID: {channel_id}")
            return

        buildable, part = mapping
        stocking_cog = self.bot.get_cog("StockingCog")
        if not stocking_cog or not hasattr(stocking_cog, "award_part"):
            logger.warning("StockingCog is not loaded or lacks 'award_part'.")
            return

        for user_id in winner_ids:
            try:
                await stocking_cog.award_part(user_id, buildable, part, channel=None, announce=True)
                logger.info(f"Awarded part {part} for buildable {buildable} to user {user_id}")
            except Exception as e:
                logger.exception(f"Failed to award part {part} to user {user_id}: {str(e)}")

    async def _extract_winner_ids(self, message: discord.Message) -> List[int]:
        """Extract winner IDs."""
        winner_ids = [mention.id for mention in message.mentions] or re.findall(r"<@!?(\d+)>", message.content)
        if winner_ids:
            return map(int, winner_ids)

        # Search previous messages
        async for prev_message in message.channel.history(limit=5, before=message.created_at, oldest_first=False):
            combined_text = f"{prev_message.content or ''} {' '.join(embed.description or '' for embed in prev_message.embeds)}"
            if WINNER_TITLE_RE.search(combined_text):
                winner_ids = re.findall(r"<@!?(\d+)>", combined_text)
                return list(map(int, winner_ids))

        logger.debug("No winners detected.")
        return []

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Detect and respond to messages."""
        if not message.guild or int(message.author.id) not in self.rumble_bot_ids:
            return

        try:
            winner_ids = await self._extract_winner_ids(message)
            if winner_ids:
                await self._handle_awards(winner_ids, message.channel.id)
        except Exception as e:
            logger.exception(f"Failed to process message: {str(e)}")


async def setup(bot: commands.Bot):
    await bot.add_cog(RumbleListenerCog(bot))