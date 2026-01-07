# cogs/rumble_listener_cog.py
"""
Rumble listener cog with persisted message-id dedupe.

Notes:
- This cog will create data/rumble_processed.json if missing.
- It will load rumble_bot_ids from data/rumble_listener_config.json if present,
  otherwise falls back to a sensible default set (you should adjust if needed).
- Integrate your existing award handling logic in the _handle_awards method
  where TODO markers are placed.
"""

from __future__ import annotations
import re
import json
import logging
import asyncio
from pathlib import Path
from typing import Set, List, Optional

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)

DATA_DIR = Path("data")
DEFAULT_RUMBLE_BOT_IDS = {693167035068317736}  # adjust if you have different IDs

MENTION_RE = re.compile(r"<@!?(\d+)>")
NUM_ID_RE = re.compile(r"\b(\d{17,20})\b")  # crude numeric id finder


class RumbleListenerCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data_dir = DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # config
        self.config_file = self.data_dir / "rumble_listener_config.json"
        self.rumble_bot_ids = set(DEFAULT_RUMBLE_BOT_IDS)
        self._load_config()

        # processed message dedupe
        self._processed_file = self.data_dir / "rumble_processed.json"
        self._processed_message_ids: Set[str] = set()
        self._load_processed()

    # -------------------------
    # config/load/save helpers
    # -------------------------
    def _load_config(self) -> None:
        try:
            if self.config_file.exists():
                with self.config_file.open("r", encoding="utf-8") as fh:
                    cfg = json.load(fh) or {}
                # prefer list in file, accept either "rumble_bot_ids" or "rumble_bot_id"
                ids = cfg.get("rumble_bot_ids") or cfg.get("rumble_bot_id")
                if isinstance(ids, list):
                    self.rumble_bot_ids = set(int(x) for x in ids)
                elif ids is not None:
                    # single id
                    self.rumble_bot_ids = {int(ids)}
                # optional channel->part map is left untouched here
        except Exception:
            logger.exception("Failed to load rumble_listener_config.json; using defaults")

    def _load_processed(self) -> None:
        try:
            if self._processed_file.exists():
                with self._processed_file.open("r", encoding="utf-8") as fh:
                    ids = json.load(fh) or []
                self._processed_message_ids = set(str(x) for x in ids)
            else:
                # create an empty file to simplify later assumptions
                self._processed_file.write_text("[]", encoding="utf-8")
                self._processed_message_ids = set()
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            out = {
                "rumble_bot_ids": self.rumble_bot_ids,
                    "rumble_bot_id": int(self.rumble_bot_ids[0]) if self.rumble_bot_ids else None,
                "channel_part_map": {str(k): [v[0], v[1]] for k, v in self.channel_part_map.items()},
            }
            with CONFIG_FILE.open("w", encoding="utf-8") as fh:
                json.dump(out, fh, ensure_ascii=False, indent=2)
            logger.info("Saved rumble listener config to %s", CONFIG_FILE)
        except Exception:
            logger.exception("Failed to load processed rumble message ids")
            self._processed_message_ids = set()

    async def _save_processed(self) -> None:
        """
        Persist the processed message ids atomically.
        """
        try:
            tmp = self._processed_file.with_suffix(".tmp")
            # ensure parent exists
            tmp.parent.mkdir(parents=True, exist_ok=True)
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(list(self._processed_message_ids), fh)
            tmp.replace(self._processed_file)
        except Exception:
            logger.exception("Failed to save processed rumble message ids")

    # -------------------------
    # message handling
    # -------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """
        Handle incoming messages from Rumble bot(s).
        - Skip messages not from configured rumble_bot_ids.
        - Skip messages we've already processed (by message.id).
        - Parse winner user ids and delegate to award handling.
        """
        try:
            if message.author is None:
                return

            try:
                author_id = int(message.author.id)
            except Exception:
                # unknown author id format - skip
                return

            if author_id not in self.rumble_bot_ids:
                # message not from a configured rumble bot
                return

            mid = getattr(message, "id", None)
            if mid is not None and str(mid) in self._processed_message_ids:
                logger.debug("Skipping already-processed rumble message id=%s", mid)
                return

            # Parse winners from message content:
            winner_ids = self._parse_winner_ids(message.content, message)

            if not winner_ids:
                logger.debug("No winner IDs parsed from rumble message id=%s", mid)
                return

            # Delegate to award logic; integrate your existing logic in _handle_awards.
            handled_ok = await self._handle_awards(winner_ids, message.channel)

            if handled_ok:
                # record and persist the message id to avoid duplicates
                try:
                    if mid is not None:
                        self._processed_message_ids.add(str(mid))
                        loop = getattr(self.bot, "loop", None)
                        if loop and loop.is_running():
                            loop.create_task(self._save_processed())
                        else:
                            # synchronous fallback
                            await self._save_processed()
                except Exception:
                    logger.exception("Failed to record processed message id %s", mid)

        except Exception:
            logger.exception("Unhandled exception in RumbleListenerCog.on_message")

    # -------------------------
    # parsing helpers
    # -------------------------
    def _parse_winner_ids(self, content: str, message: discord.Message) -> List[int]:
        """
        Return a list of integer user IDs parsed from message content.
        - Looks for explicit mentions (<@1234567890>) first.
        - Falls back to numeric IDs (17-20 digit tokens).
        - If none found, also inspect message.embeds for potential mention text.
        """
        ids: List[int] = []

        # mentions in plain content
        for m in MENTION_RE.findall(content or ""):
            try:
                ids.append(int(m))
            except Exception:
                continue

        # numeric tokens
        for m in NUM_ID_RE.findall(content or ""):
            try:
                ids.append(int(m))
            except Exception:
                continue

        # also check message.mentions list (discord auto-parsed)
        try:
            for u in getattr(message, "mentions", []) or []:
                try:
                    ids.append(int(u.id))
                except Exception:
                    continue
        except Exception:
            pass

        # check embeds for mention-like text (best-effort)
        try:
            for emb in getattr(message, "embeds", []) or []:
                text = ""
                if emb.title:
                    text += str(emb.title) + " "
                if emb.description:
                    text += str(emb.description) + " "
                for m in MENTION_RE.findall(text):
                    try:
                        ids.append(int(m))
                    except Exception:
                        continue
                for m in NUM_ID_RE.findall(text):
                    try:
                        ids.append(int(m))
                    except Exception:
                        continue
        except Exception:
            pass

        # de-duplicate while preserving order
        seen = set()
        out: List[int] = []
        for i in ids:
            if i not in seen:
                seen.add(i)
                out.append(i)
        return out

    # -------------------------
    # award handling (hook)
    # -------------------------
    async def _handle_awards(self, winner_ids: List[int], channel: discord.abc.Messageable) -> bool:
        """
        Apply awards to the given winner IDs.

        IMPORTANT: Replace this method's internals with your actual award logic
        (the routine that grants pieces/stickers/stockings, sends messages, etc).

        Return True if awards were applied successfully (so the message id is marked processed).
        Return False if nothing was applied or an error occurred (so the message can be retried).
        """
        try:
            logger.info("Rumble winners parsed: %s (channel=%s)", winner_ids, getattr(channel, "id", None))

            # ------- TODO: integrate your existing award logic here -------
            # Example possibilities:
            #   - import the function you already use to award parts and call it:
            #         from .some_other_module import award_users_from_rumble
            #         await award_users_from_rumble(winner_ids, channel)
            #
            #   - or if your cog previously had code here, paste it inside this try-block.
            #
            # Keep this method async and return True when your award persist/save succeeds.
            #
            # For now we log and return True to mark the message processed.
            # Remove or change that once you insert your real award calls.
            # ----------------------------------------------------------------

            # Simulate success (replace with real work)
            return True

        except Exception:
            logger.exception("Error while handling awards for winners %s", winner_ids)
            return False


# Cog setup
def setup(bot: commands.Bot):
    bot.add_cog(RumbleListenerCog(bot))