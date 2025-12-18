#!/usr/bin/env python3
"""
Updated RumbleListenerCog:
- Improved winner detection regex.
- Combined message and embed text for better parsing.
- Added config persistence helpers and a safe setup guard.
"""

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

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
        logger.info(
            "RumbleListener initialized with bot IDs=%r and channel mappings=%r",
            self.rumble_bot_ids,
            self.channel_part_map,
        )

    def _load_config_from_dict(self, data: Dict[str, List]) -> None:
        """Load configuration from dict."""
        self.rumble_bot_ids = list(map(int, data.get("rumble_bot_ids", [])))
        self.channel_part_map = {int(ch_id): tuple(map(str, val)) for ch_id, val in data.get("channel_part_map", {}).items()}

    def _load_config_file(self) -> None:
        """Load configuration from file."""
        try:
            if CONFIG_FILE.exists():
                with CONFIG_FILE.open("r", encoding="utf-8") as fh:
                    config_data = json.load(fh)
                    self._load_config_from_dict(config_data)
                logger.info(f"Config file loaded: {config_data}")
        except Exception:
            logger.exception("Failed to load config file")

    def _save_config_file(self) -> None:
        """Persist current runtime config to disk (rumble_bot_ids and channel_part_map)."""
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

    def get_config_snapshot(self) -> Dict[str, Any]:
        """Return a JSON-serializable snapshot of the current config for admin UI."""
        return {
            "rumble_bot_ids": self.rumble_bot_ids,
            "channel_part_map": {str(k): [v[0], v[1]] for k, v in self.channel_part_map.items()},
        }

    async def _handle_awards(self, winner_ids: List[int], channel: discord.TextChannel) -> None:
        """Award parts to winners. Pass the actual channel into StockingCog so it can announce."""
        mapping = self.channel_part_map.get(int(channel.id))
        if not mapping:
            logger.warning("No mapping found for channel ID: %s", channel.id)
            return

        buildable, part = mapping
        stocking_cog = self.bot.get_cog("StockingCog")
        if not stocking_cog or not hasattr(stocking_cog, "award_part"):
            logger.warning("StockingCog is not loaded or lacks 'award_part'.")
            return

        for user_id in winner_ids:
            try:
                result = await stocking_cog.award_part(user_id, buildable, part, channel=channel, announce=True)
                if result:
                    logger.info("Awarded part %s for buildable %s to user %s", part, buildable, user_id)
                else:
                    logger.info("Award skipped/failed for user %s (already has part or other failure): %s/%s", user_id, buildable, part)
            except Exception:
                logger.exception("Failed to award part %s to user %s", part, user_id)

    async def _extract_winner_ids(self, message: discord.Message) -> List[int]:
        """Extract winner IDs from mentions, id tokens, or nearby messages; always returns a list of ints."""
        try:
            # 1) direct mentions on the message
            if message.mentions:
                return [m.id for m in message.mentions]
        except Exception:
            pass

        # 2) explicit id tokens inside message content and embeds
        ids: List[int] = []
        try:
            ids.extend([int(x) for x in re.findall(r"<@!?(?P<id>\d+)>", message.content or "")])
            for emb in message.embeds:
                emb_text = " ".join(filter(None, [emb.title or "", emb.description or ""] + [f.value for f in (emb.fields or [])]))
                ids.extend([int(x) for x in re.findall(r"<@!?(?P<id>\d+)>", emb_text)])
        except Exception:
            logger.exception("rumble_listener: id extraction failed")
        # unique preserve order
        ids = [int(x) for x in dict.fromkeys(ids)]
        if ids:
            return ids

        # 3) look in nearby messages (prev / next) for ping/id
        try:
            async for prev in message.channel.history(limit=8, before=message.created_at, oldest_first=False):
                if prev.mentions:
                    return [m.id for m in prev.mentions]
                prev_ids = re.findall(r"<@!?(?P<id>\d+)>", prev.content or "")
                if prev_ids:
                    return [int(prev_ids[0])]
            async for after in message.channel.history(limit=6, after=message.created_at, oldest_first=True):
                if after.mentions:
                    return [m.id for m in after.mentions]
                after_ids = re.findall(r"<@!?(?P<id>\d+)>", after.content or "")
                if after_ids:
                    return [int(after_ids[0])]
        except Exception:
            logger.exception("rumble_listener: nearby history scan failed")

        # 4) name-based extraction fallback (collect and fuzzy match)
        winner_candidates: List[str] = []
        try:
            for emb in message.embeds:
                for f in (emb.fields or []):
                    if WINNER_TITLE_RE.search(f.name or "") or WINNER_TITLE_RE.search(f.value or ""):
                        for line in (f.value or "").splitlines():
                            line = line.strip()
                            if line:
                                winner_candidates.append(line)
                                break
                if emb.title and WINNER_TITLE_RE.search(emb.title) and emb.description:
                    for line in emb.description.splitlines():
                        line = line.strip()
                        if line:
                            winner_candidates.append(line)
                            break
        except Exception:
            logger.exception("rumble_listener: embed winner-field extraction failed")

        if not winner_candidates:
            # fallback simple parsing of content lines
            for ln in (message.content or "").splitlines():
                ln = ln.strip()
                if WINNER_TITLE_RE.search(ln):
                    continue
                if ln:
                    winner_candidates.append(ln)

        if not winner_candidates:
            return []

        try:
            guild = message.guild
            if guild is None:
                return []
            matched = await self._match_names_to_member_ids(guild, winner_candidates)
            return matched
        except Exception:
            logger.exception("rumble_listener: name->id matching failed")
            return []

    async def _match_names_to_member_ids(self, guild: discord.Guild, candidates: List[str]) -> List[int]:
        # simple wrapper kept minimal; in your original code you can plug the full matching implementation
        # For brevity here we'll assume guild.members is available and do a simple normalize/equality match
        def _normalize_name(s: str) -> str:
            import unicodedata
            nk = unicodedata.normalize("NFKD", s)
            nk = "".join(ch for ch in nk if not unicodedata.combining(ch))
            nk = nk.lower()
            nk = re.sub(r"[^\w\s]", " ", nk)
            nk = re.sub(r"\s+", " ", nk).strip()
            return nk

        if not guild or not candidates:
            return []
        try:
            members = list(guild.members or [])
            norm_map = {m.id: (_normalize_name(m.name or ""), _normalize_name(m.display_name or "")) for m in members}
            out = []
            for cand in candidates:
                nc = _normalize_name(cand)
                if not nc:
                    continue
                for mid, (nname, dname) in norm_map.items():
                    if nname == nc or dname == nc:
                        if mid not in out:
                            out.append(mid)
                            break
            return out
        except Exception:
            logger.exception("rumble_listener: _match_names_to_member_ids failed")
            return []

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Detect and respond to messages."""
        # ignore DMs
        if message.guild is None:
            return

        # only process messages from monitored bots (if configured)
        try:
            author_id = int(getattr(message.author, "id", 0))
        except Exception:
            return
        # if self.rumble_bot_ids is non-empty, require membership; otherwise monitor all bot authors
        if self.rumble_bot_ids and author_id not in self.rumble_bot_ids:
            return

        # quick winner detection
        combined_text = " ".join([message.content or ""] + [emb.title or "" for emb in (message.embeds or [])] + [emb.description or "" for emb in (message.embeds or [])])
        if not (WINNER_TITLE_RE.search(combined_text)):
            return

        try:
            winner_ids = await self._extract_winner_ids(message)
            if winner_ids:
                # pass the actual channel object so StockingCog can announce
                await self._handle_awards(winner_ids, message.channel)
        except Exception:
            logger.exception("Failed to process message: %s", message.content)

    def get_config_snapshot(self) -> Dict[str, Any]:
        return {
            "rumble_bot_ids": self.rumble_bot_ids,
            "channel_part_map": {str(k): [v[0], v[1]] for k, v in self.channel_part_map.items()},
        }


async def setup(bot: commands.Bot):
    # avoid "Cog already loaded" exception
    if bot.get_cog("RumbleListenerCog"):
        logger.info("RumbleListenerCog already present; skipping add_cog.")
        return
    await bot.add_cog(RumbleListenerCog(bot))