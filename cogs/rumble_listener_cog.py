#!/usr/bin/env python3
"""
Refactored RumbleListenerCog with mapping persistence and improved winner detection.

Enhancements:
- Fixed issues with `_save_config_file` to ensure mappings persist to `rumble_listener_config.json`.
- Expanded regex handling for decorative "WINNER" messages.
- Improved debugging logs for troubleshooting.

Processes messages authored by bot IDs in `rumble_listener_config.json` and only in channels listed in `channel_part_map`.
"""

import asyncio
import json
import logging
import re
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)

from utils.snowman_theme import DEFAULT_COLOR, generate_part_maps_from_buildables

DATA_DIR = Path("data")
CONFIG_FILE = DATA_DIR / "rumble_listener_config.json"

# Improved regex for WINNER detection
WINNER_TITLE_RE = re.compile(r"(?i)\bwin(?:ner|ners)?\b|w(?:o|0)n(?:!+|\s+)?")
ADDITIONAL_WIN_RE = re.compile(r"\b(found (?:a|an)|received|was awarded|winner|won)\b", re.IGNORECASE)

PART_EMOJI, PART_COLORS = generate_part_maps_from_buildables()


def ensure_data_dir():
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
        logger.info(
            "RumbleListener initialized with bot IDs=%r and channel mappings=%r",
            self.rumble_bot_ids,
            self.channel_part_map,
        )

    def _load_config_from_dict(self, data: Dict[str, List]) -> None:
        self.rumble_bot_ids = list(map(int, data.get("rumble_bot_ids", [])))
        self.channel_part_map = {
            int(ch_id): tuple(map(str, value))
            for ch_id, value in data.get("channel_part_map", {}).items()
        }

    def _load_config_file(self) -> None:
        try:
            if CONFIG_FILE.exists():
                with CONFIG_FILE.open("r", encoding="utf-8") as fh:
                    config_data = json.load(fh)
                    self._load_config_from_dict(config_data)
        except Exception:
            logger.exception("Failed to load configuration file.")

    def _save_config_file(self) -> None:
        try:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            config = {
                "rumble_bot_ids": self.rumble_bot_ids,
                "channel_part_map": {str(k): list(v) for k, v in self.channel_part_map.items()},
            }
            with CONFIG_FILE.open("w", encoding="utf-8") as fh:
                json.dump(config, fh, ensure_ascii=False, indent=2)
            logger.info("Configuration saved to %s", CONFIG_FILE)
        except Exception:
            logger.exception("Failed to save configuration file.")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen for messages and process potential winner announcements."""
        # Ignore DMs
        if not message.guild:
            return

        # Check if author is one of the bot IDs
        if int(message.author.id) not in self.rumble_bot_ids:
            return

        # Quick winner detection check
        if not (
            WINNER_TITLE_RE.search(message.content) or
            any(
                WINNER_TITLE_RE.search(embed.title or "") or WINNER_TITLE_RE.search(embed.description or "")
                for embed in message.embeds
            )
        ):
            return

        logger.debug("Processing message in channel %s: %s", message.channel.id, message.content)

        try:
            # Extract winners and perform award logic
            winner_ids = await self._extract_winner_ids(message)
            if winner_ids:
                await self._handle_awards(winner_ids, message.channel.id)
        except Exception:
            logger.exception("Failed to handle awards for message: %s", message.content)

    async def _extract_winner_ids(self, message: discord.Message) -> List[int]:
        """Extract winner IDs based on mentions and content."""
        # Extract from mentions
        winner_ids = [mention.id for mention in message.mentions]
        if winner_ids:
            return winner_ids

        # Extract from text
        winner_ids = list(map(int, re.findall(r"<@!?(\d+)>", message.content)))
        return winner_ids

    async def _handle_awards(self, winner_ids: List[int], channel_id: int) -> None:
        """Handle part awards for identified winners."""
        mapping = self.channel_part_map.get(channel_id)
        if not mapping:
            logger.warning("No mapping found for channel %s", channel_id)
            return

        buildable, part = mapping
        logger.info("Awarding part '%s' for buildable '%s' to users: %s", part, buildable, winner_ids)
        stocking_cog = self.bot.get_cog("StockingCog")
        if not stocking_cog:
            logger.warning("StockingCog is not loaded.")
            return

        for user_id in winner_ids:
            try:
                if hasattr(stocking_cog, "award_part"):
                    await stocking_cog.award_part(user_id, buildable, part, channel=None, announce=True)
                else:
                    logger.warning("StockingCog lacks 'award_part' method.")
            except Exception:
                logger.exception("Failed to award part '%s' to user %s", part, user_id)


async def setup(bot: commands.Bot):
    await bot.add_cog(RumbleListenerCog(bot))