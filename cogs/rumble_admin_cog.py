#!/usr/bin/env python3
"""
Refactored RumbleListenerCog with improved winner detection and award handling.

Enhancements:
- Expanded regex handling to support decorative "WINNER" text and variations.
- Improved nearby message scanning to detect winners.
- Enhanced debugging logs for troubleshooting.
- Added robust award logic to ensure mappings are honored.

Processes messages authored by bot IDs in `rumble_listener_config.json` and only in channels listed in `channel_part_map`.
"""

import asyncio
import json
import logging
import re
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)

from utils.snowman_theme import DEFAULT_COLOR, generate_part_maps_from_buildables

DATA_DIR = Path("data")
CONFIG_FILE = DATA_DIR / "rumble_listener_config.json"

# Improved regex for WINNER detection
WINNER_TITLE_RE = re.compile(r"(?i)\bwin(?:ner|ners)?\b|w(?:o|0)n(?:!+|\s+)?")
ADDITIONAL_WIN_RE = re.compile(r"\b(found|reward|received|awarded|multiplier|prize)\b", re.IGNORECASE)

PART_EMOJI, PART_COLORS = generate_part_maps_from_buildables()


def ensure_data_dir():
    """Ensure the data directory exists."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)


class RumbleListenerCog(commands.Cog):
    def __init__(self, bot: commands.Bot, initial_config: Optional[Dict[str, Any]] = None):
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

    def _load_config_from_dict(self, data: Dict[str, Any]) -> None:
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
        except Exception:
            logger.exception("Failed to load configuration file.")

    def _save_config_file(self) -> None:
        """Save the current configuration to a file."""
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

    @staticmethod
    def _extract_ids_from_text(text: str) -> List[int]:
        """Extract IDs directly from text."""
        ids = re.findall(r"<@!?(\d+)>", text)
        return [int(x) for x in ids]

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen for messages and process potential winner announcements."""
        # Ignore DMs
        if not message.guild:
            return

        # Check if author is one of the bot IDs
        if int(message.author.id) not in self.rumble_bot_ids:
            return

        # Combine message content and embed data for winner detection
        combined_message_text = " ".join(
            [
                message.content or "",
                *[
                    embed.title or "" for embed in message.embeds
                ],
                *[
                    embed.description or "" for embed in message.embeds
                ]
            ]
        )

        # Quick winner detection check
        if not (WINNER_TITLE_RE.search(combined_message_text) or ADDITIONAL_WIN_RE.search(combined_message_text)):
            logger.debug("No winner detected in message: %s", combined_message_text)
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
        winner_ids = self._extract_ids_from_text(message.content)
        if winner_ids:
            return winner_ids

        # Check previous messages for additional context
        async for prev_message in message.channel.history(limit=5, before=message.created_at, oldest_first=False):
            combined_text = " ".join(
                [prev_message.content or ""] +
                [embed.title or "" for embed in prev_message.embeds] +
                [embed.description or "" for embed in prev_message.embeds]
            )
            if WINNER_TITLE_RE.search(combined_text) or ADDITIONAL_WIN_RE.search(combined_text):
                winner_ids = self._extract_ids_from_text(combined_text)
                if winner_ids:
                    logger.debug("Winner IDs from previous context: %s", winner_ids)
                    return winner_ids

        return []

    async def _handle_awards(self, winner_ids: List[int], channel_id: int) -> None:
        """Handle part awards for identified winners."""
        mapping = self.channel_part_map.get(channel_id)
        if not mapping:
            logger.warning("No mapping found for channel %s", channel_id)
            return

        stocking_cog = self.bot.get_cog("StockingCog")
        if not stocking_cog or not hasattr(stocking_cog, "award_part"):
            logger.warning("StockingCog is not loaded or lacks 'award_part'.")
            return

        buildable, part = mapping
        logger.info("Awarding part '%s' for buildable '%s' to users: %s", part, buildable, winner_ids)
        for user_id in winner_ids:
            try:
                await stocking_cog.award_part(user_id, buildable, part, channel=None, announce=True)
            except Exception:
                logger.exception("Failed to award part '%s' to user %s", part, user_id)


async def setup(bot: commands.Bot):
    await bot.add_cog(RumbleListenerCog(bot))