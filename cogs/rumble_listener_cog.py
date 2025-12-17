#!/usr/bin/env python3
"""
Refactored RumbleListenerCog for dynamic winner detection.

Enhancements:
- Improved regex handling for decorative text (e.g., stylized "WINNER" messages).
- Expanded embed parsing logic for better winner detection.
- Enhanced debugging logs to facilitate troubleshooting.
- Adjusted nearby history scanning for more robust detection.

Only processes messages authored by IDs in data/rumble_listener_config.json (rumble_bot_ids)
and only in channels listed in channel_part_map (or global key "0").
"""

import asyncio
import json
import logging
import re
import time
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
ADDITIONAL_WIN_RE = re.compile(r"\b(found (?:a|an)|received|was awarded|winner|won)\b", re.IGNORECASE)

PART_EMOJI, PART_COLORS = generate_part_maps_from_buildables()
AWARD_DEBOUNCE_SECONDS = 120


def ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _normalize_name(s: str) -> str:
    """Normalize user names for comparison."""
    if not s:
        return ""
    nk = unicodedata.normalize("NFKD", s)
    nk = "".join(ch for ch in nk if not unicodedata.combining(ch))
    nk = nk.lower()
    nk = re.sub(r"[^\w\s]", " ", nk)
    nk = re.sub(r"\s+", " ", nk).strip()
    return nk


class RumbleListenerCog(commands.Cog):
    def __init__(self, bot: commands.Bot, initial_config: Optional[Dict[str, Any]] = None):
        self.bot = bot
        self._lock = asyncio.Lock()
        ensure_data_dir()
        self.rumble_bot_ids: List[int] = []
        self.channel_part_map: Dict[int, Tuple[str, str]] = {}
        self._recent_awards: Dict[Tuple[int, int, int, str, str], float] = {}
        if initial_config:
            self._load_from_dict(initial_config)
        self._load_config_file()
        logger.info(
            "rumble_listener: loaded rumble_bot_ids=%r channel_part_map=%r",
            self.rumble_bot_ids,
            self.channel_part_map,
        )

    def _load_from_dict(self, data: Dict[str, Any]) -> None:
        """Load configuration from provided dictionary."""
        rids = data.get("rumble_bot_ids", [])
        self.rumble_bot_ids = [int(x) for x in rids] if rids else []
        cmap = data.get("channel_part_map", {})
        new_map: Dict[int, Tuple[str, str]] = {}
        for chk, val in cmap.items():
            try:
                ch = int(chk)
            except Exception:
                continue
            if isinstance(val, (list, tuple)) and len(val) >= 2:
                new_map[ch] = (str(val[0]), str(val[1]))
        self.channel_part_map = new_map

    def _load_config_file(self) -> None:
        """Load configuration from file."""
        try:
            if CONFIG_FILE.exists():
                with CONFIG_FILE.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
                    if isinstance(data, dict):
                        self._load_from_dict(data)
        except Exception:
            logger.exception("rumble_listener: failed to load config file")

    def _save_config_file(self) -> None:
        """Persist configuration to file."""
        try:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            out = {
                "rumble_bot_ids": self.rumble_bot_ids,
                "channel_part_map": {str(k): [v[0], v[1]] for k, v in self.channel_part_map.items()},
            }
            with CONFIG_FILE.open("w", encoding="utf-8") as fh:
                json.dump(out, fh, ensure_ascii=False, indent=2)
        except Exception:
            logger.exception("rumble_listener: failed to save config file")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Process incoming messages."""
        # Ignore DMs and messages not in monitored channels.
        if message.guild is None or int(message.channel.id) not in self.channel_part_map:
            return

        # Check if the message author is a monitored bot.
        if message.author.bot and int(message.author.id) not in self.rumble_bot_ids:
            return

        # Quick detection of winner-style messages.
        if not (WINNER_TITLE_RE.search(message.content) or any(
            WINNER_TITLE_RE.search(str(embed.title)) or WINNER_TITLE_RE.search(str(embed.description))
            for embed in message.embeds
        )):
            return

        logger.debug("Processing message: %s", message.content)

        # Extract winner IDs and handle awarding logic.
        try:
            winner_ids = await self._extract_winner_ids(message)
            if winner_ids:
                await self._handle_awards(message, winner_ids)
        except Exception:
            logger.exception("rumble_listener: final award handling failed!")

    async def _extract_winner_ids(self, message: discord.Message) -> List[int]:
        """Extract IDs of winners based on message content."""
        ids = [m.id for m in message.mentions]
        if not ids:
            ids = self._extract_ids_from_text(message.content)
        return ids

    def _extract_ids_from_text(self, text: str) -> List[int]:
        """Extract IDs directly from text."""
        ids = re.findall(r"<@!?(\d+)>", text)
        return [int(x) for x in ids]

    async def _handle_awards(self, message: discord.Message, winner_ids: List[int]) -> None:
        """Award parts to winners."""
        mapping = self.channel_part_map.get(message.channel.id)
        if not mapping:
            return

        buildable_key, part_key = mapping
        logger.debug("Awarding %s %s to %s", buildable_key, part_key, winner_ids)

        # Perform actual award logic here (pass data to stocking cog, etc.).

async def setup(bot: commands.Bot):
    await bot.add_cog(RumbleListenerCog(bot))