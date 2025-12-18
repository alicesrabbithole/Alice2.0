#!/usr/bin/env python3
"""
Updated RumbleListenerCog:
- Improved winner detection with enhanced regex.
- Verifies StockingCog loading before awarding parts.
- Expanded logging and debugging for troubleshooting.
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

# Enhanced regex for winner detection
WINNER_TITLE_RE = re.compile(r"(?i)\b(?:win(?:ner|ners)?|:crwn2:|__winner__|won|reward)\b", re.IGNORECASE)

def ensure_data_dir():
    """Ensure the data directory exists."""
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
        logger.info(f"RumbleListener initialized with bot IDs={self.rumble_bot_ids} and channel mappings={self.channel_part_map}")

    def _load_config_from_dict(self, data: Dict[str, List]) -> None:
        """Load configuration from a dictionary."""
        self.rumble_bot_ids = list(map(int, data.get("rumble_bot_ids", [])))
        self.channel_part_map = {
            int(ch_id): tuple(map(str, value))
            for ch_id, value in data.get("channel_part_map", {}).items()
        }

    def _load_config_file(self) -> None:
        """Load configuration from a file."""
        try:
            if CONFIG_FILE.exists():
                with CONFIG_FILE.open("r", encoding="utf-8") as fh:
                    config_data = json.load(fh)
                    self._load_config_from_dict(config_data)
        except Exception as e:
            logger.exception(f"Failed to load configuration file: {str(e)}")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen for messages and detect winners."""
        if not message.guild:
            return

        if int(message.author.id) not in self.rumble_bot_ids:
            return

        logger.debug(f"Processing message in channel {message.channel.id}: {message.content}")

        # Extract winners and award logic
        try:
            winner_ids = await self._extract_winner_ids(message)
            if winner_ids:
                await self._handle_awards(winner_ids, message.channel.id)
        except Exception as e:
            logger.exception(f"Error handling awards: {str(e)}")

    async def _extract_winner_ids(self, message: discord.Message) -> List[int]:
        """Extract winner IDs from message or history."""
        winner_ids = [mention.id for mention in message.mentions]

        if not winner_ids:
            winner_ids = list(map(int, re.findall(r"<@!?(\d+)>", message.content)))

        if winner_ids:
            return winner_ids

        # Scan previous messages if needed for context
        async for prev_message in message.channel.history(limit=5, before=message.created_at, oldest_first=False):
            combined_text = f"{prev_message.content or ''} {' '.join(embed.description or '' for embed in prev_message.embeds)}"
            if WINNER_TITLE_RE.search(combined_text):
                winner_ids = list(map(int, re.findall(r"<@!?(\d+)>", combined_text)))
                if winner_ids:
                    logger.debug(f"Winner IDs from history: {winner_ids}")
                    return winner_ids

        logger.debug("No winners detected.")
        return []

    async def _handle_awards(self, winner_ids: List[int], channel_id: int) -> None:
        """Award parts to identified winners."""
        mapping = self.channel_part_map.get(channel_id)
        if not mapping:
            logger.warning(f"No mapping found for channel {channel_id}")
            return

        stocking_cog = self.bot.get_cog("StockingCog")
        if not stocking_cog or not hasattr(stocking_cog, "award_part"):
            logger.warning("StockingCog is not loaded or lacks 'award_part' method.")
            return

        buildable, part = mapping
        for user_id in winner_ids:
            try:
                await stocking_cog.award_part(user_id, buildable, part, channel=None, announce=True)
                logger.info(f"Awarded part {part} for buildable {buildable} to user {user_id}")
            except Exception as e:
                logger.exception(f"Failed to award part {part} to user {user_id}: {str(e)}")


async def setup(bot: commands.Bot):
    await bot.add_cog(RumbleListenerCog(bot))