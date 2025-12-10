import asyncio
import discord
from discord.ext import commands
import json
import os
import tempfile
import logging
from typing import Dict, Optional

from utils.checks import is_staff
from utils.theme import Colors, Emojis
import config

logger = logging.getLogger(__name__)

STICKY_DATA_FILE = config.DATA_DIR / "stickies.json"
DEFAULT_MESSAGE_THRESHOLD = 5

# Set of excluded channel IDs (as strings) where stickies will NOT work.
EXCLUDED_CHANNELS = set()


class StickyCog(commands.Cog, name="Sticky Messages"):
    """Manages sticky messages that repost after a set number of messages."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.stickies: Dict[str, Dict] = {}
        self.message_counts: Dict[str, int] = {}
        self._locks: Dict[str, asyncio.Lock] = {}
        self.load_stickies()

    def load_stickies(self):
        try:
            if STICKY_DATA_FILE.exists():
                with STICKY_DATA_FILE.open("r", encoding="utf-8") as f:
                    self.stickies = json.load(f) or {}
                    logger.info("Loaded %d stickies from %s", len(self.stickies), STICKY_DATA_FILE)
            else:
                self.stickies = {}
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.exception("Failed to load stickies from %s: %s", STICKY_DATA_FILE, e)
            self.stickies = {}

    def save_stickies(self):
        try:
            STICKY_DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
            # Atomic write: write to temp and os.replace
            fd, tmp = tempfile.mkstemp(dir=str(STICKY_DATA_FILE.parent))
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.stickies, f, indent=4)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, str(STICKY_DATA_FILE))
            logger.info("Saved %d stickies to %s", len(self.stickies), STICKY_DATA_FILE)
        except Exception:
            logger.exception("Failed to save stickies to %s", STICKY_DATA_FILE)

    def _get_lock(self, channel_id: str) -> asyncio.Lock:
        lock = self._locks.get(channel_id)
        if not lock:
            lock = asyncio.Lock()
            self._locks[channel_id] = lock
        return lock

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Only check channels that are not excluded, and ignore DMs/bots
        if message.guild is None or message.author.bot:
            return

        channel_id = str(message.channel.id)
        if channel_id in EXCLUDED_CHANNELS:
            return

        sticky_info = self.stickies.get(channel_id)
        if not sticky_info:
            return

        if sticky_info.get("disabled"):
            return

        # Use per-sticky interval when configured, otherwise fallback to DEFAULT_MESSAGE_THRESHOLD
        interval = int(sticky_info.get("interval", DEFAULT_MESSAGE_THRESHOLD))
        self.message_counts[channel_id] = self.message_counts.get(channel_id, 0) + 1
        if self.message_counts[channel_id] >= interval:
            self.message_counts[channel_id] = 0
            # Run repost under per-channel lock to avoid concurrent reposts/deletes
            lock = self._get_lock(channel_id)
            async with lock:
                await self.repost_sticky(message.channel)

    async def repost_sticky(self, channel: discord.TextChannel):
        channel_id = str(channel.id)
        sticky_info = self.stickies.get(channel_id)
        if not sticky_info or sticky_info.get("disabled"):
            return

        last_message_id = sticky_info.get("last_message_id")

        # Try to edit the previous bot message first (less noisy). If not possible, send a new one.
        if last_message_id:
            try:
                old_message = await channel.fetch_message(int(last_message_id))
                if old_message and old_message.author and old_message.author.id == self.bot.user.id:
                    try:
                        # If message exists and is ours, try to edit it.
                        await old_message.edit(embed=discord.Embed(description=sticky_info.get("message", ""), color=Colors.PRIMARY))
                        # last_message_id stays the same
                        logger.info("Edited existing sticky in channel %s (msg=%s)", channel_id, last_message_id)
                        return
                    except discord.Forbidden:
                        # Can't edit — fall through to sending a new message
                        logger.warning("No permission to edit old sticky in channel %s; will send a new sticky", channel_id)
                    except discord.HTTPException:
                        logger.debug("Failed to edit old sticky %s in %s, will send new one", last_message_id, channel_id, exc_info=True)
                # If old message isn't ours, we'll just send a new one and try to clean up old one
            except discord.NotFound:
                logger.debug("Previous sticky message %s not found in channel %s (deleted)", last_message_id, channel_id)
            except Exception:
                logger.exception("Error fetching previous sticky message %s in channel %s", last_message_id, channel_id)

        # Send a new message
        embed = discord.Embed(description=sticky_info.get("message", ""), color=Colors.PRIMARY)
        try:
            new_message = await channel.send(embed=embed)
            self.stickies[channel_id]["last_message_id"] = new_message.id
            self.save_stickies()
            logger.info("Posted sticky in channel %s (msg=%s)", channel_id, new_message.id)
        except discord.Forbidden:
            logger.warning("No permission to send sticky in channel %s; marking sticky disabled", channel_id)
            sticky_info["disabled"] = True
            self.save_stickies()
            return
        except Exception:
            logger.exception("Failed to post sticky in channel %s", channel_id)
            return

        # Best-effort: try to delete the old message if it was our bot message previously.
        if last_message_id:
            try:
                old_message = await channel.fetch_message(int(last_message_id))
                if old_message and old_message.author and old_message.author.id == self.bot.user.id:
                    try:
                        await old_message.delete()
                        logger.debug("Deleted old sticky %s in channel %s", last_message_id, channel_id)
                    except discord.Forbidden:
                        # Do NOT disable the sticky if we can't delete; just warn and keep going.
                        logger.warning("No permission to delete old sticky %s in %s; leaving it in place", last_message_id, channel_id)
                    except discord.NotFound:
                        pass
                    except Exception:
                        logger.exception("Failed to delete old sticky %s in %s", last_message_id, channel_id)
            except discord.NotFound:
                pass
            except Exception:
                logger.debug("Error fetching old sticky %s in %s", last_message_id, channel_id, exc_info=True)

        # Best-effort pin the sticky so it is less likely to be removed
        try:
            await new_message.pin(reason="Sticky message pinned by bot")
        except Exception:
            logger.debug("Could not pin sticky message; missing Manage Messages or other error", exc_info=True)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        """
        If our recorded sticky message was deleted, repost it immediately.
        Using raw event because message may not be in cache.
        """
        try:
            channel_id = str(payload.channel_id)
            msg_id = payload.message_id
            sticky = self.stickies.get(channel_id)
            if not sticky or sticky.get("disabled"):
                return
            # If their deletion matches our stored message id, repost right away
            if sticky.get("last_message_id") and int(sticky["last_message_id"]) == int(msg_id):
                channel = self.bot.get_channel(int(channel_id))
                if not channel:
                    try:
                        channel = await self.bot.fetch_channel(int(channel_id))
                    except Exception:
                        logger.exception("Could not fetch channel %s to repost sticky", channel_id)
                        return
                # Small delay to avoid race with delete triggers
                await asyncio.sleep(0.2)
                lock = self._get_lock(channel_id)
                async with lock:
                    await self.repost_sticky(channel)
        except Exception:
            logger.exception("Error in on_raw_message_delete handler", exc_info=True)

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload: discord.RawBulkMessageDeleteEvent):
        """
        Handle bulk deletes (purges). If our stored sticky id is included in the deleted ids, repost.
        """
        try:
            channel_id = str(payload.channel_id)
            deleted_ids = set(map(int, payload.message_ids))
            sticky = self.stickies.get(channel_id)
            if not sticky or sticky.get("disabled") or not sticky.get("last_message_id"):
                return
            if int(sticky["last_message_id"]) in deleted_ids:
                channel = self.bot.get_channel(int(channel_id))
                if not channel:
                    try:
                        channel = await self.bot.fetch_channel(int(channel_id))
                    except Exception:
                        logger.exception("Could not fetch channel %s to repost sticky after bulk delete", channel_id)
                        return
                await asyncio.sleep(0.2)
                lock = self._get_lock(channel_id)
                async with lock:
                    await self.repost_sticky(channel)
        except Exception:
            logger.exception("Error in on_raw_bulk_message_delete handler", exc_info=True)

    # --- User Commands ---
    @commands.hybrid_command(name="stickplz", description="Add or update a sticky.")
    @commands.guild_only()
    @is_staff()
    async def sticky_set(self, ctx: commands.Context, *, message: str):
        channel_id = str(ctx.channel.id)
        self.stickies[channel_id] = {"message": message, "last_message_id": None, "disabled": False, "interval": DEFAULT_MESSAGE_THRESHOLD}
        self.message_counts[channel_id] = 0
        self.save_stickies()

        try:
            await ctx.send(f"{Emojis.SUCCESS} Sticky message has been set. Posting it now...", ephemeral=True)
        except Exception:
            logger.debug("Failed to ack sticky_set via response; continuing")

        await self.repost_sticky(ctx.channel)

    @commands.hybrid_command(name="byesticky", description="Remove the sticky.")
    @commands.guild_only()
    @is_staff()
    async def sticky_remove(self, ctx: commands.Context):
        channel_id = str(ctx.channel.id)
        if channel_id in self.stickies:
            last_message_id = self.stickies[channel_id].get("last_message_id")
            if last_message_id:
                try:
                    old_message = await ctx.channel.fetch_message(last_message_id)
                    # delete only if it's our bot message
                    if old_message and old_message.author and old_message.author.id == self.bot.user.id:
                        await old_message.delete()
                except (discord.NotFound, discord.Forbidden):
                    pass

            del self.stickies[channel_id]
            self.message_counts.pop(channel_id, None)
            self.save_stickies()
            await ctx.send(f"{Emojis.SUCCESS} Sticky message has been removed.", ephemeral=True)
        else:
            await ctx.send(f"{Emojis.FAILURE} There is no sticky message set for this channel.", ephemeral=True)

    @commands.hybrid_command(name="liststickies", description="List configured stickies for this guild")
    @commands.guild_only()
    @is_staff()
    async def sticky_list(self, ctx: commands.Context):
        """
        List all configured stickies for the current guild, including status and interval.
        """
        guild_id = ctx.guild.id
        lines = []
        for channel_id, info in self.stickies.items():
            try:
                ch = self.bot.get_channel(int(channel_id)) or await self.bot.fetch_channel(int(channel_id))
            except Exception:
                ch = None
            # only include channels that belong to this guild
            if not ch or not ch.guild or ch.guild.id != guild_id:
                continue
            status = "disabled" if info.get("disabled") else "enabled"
            interval = info.get("interval", DEFAULT_MESSAGE_THRESHOLD)
            preview = info.get("message", "").replace("\n", " ")
            if len(preview) > 150:
                preview = preview[:147] + "..."
            last_msg = info.get("last_message_id")
            last_msg_text = f" (last msg id: {last_msg})" if last_msg else ""
            lines.append(f"{ch.mention} — interval {interval} — {status}{last_msg_text}\n> {preview}")

        if not lines:
            await ctx.send("No stickies configured for this guild.", ephemeral=True)
            return

        # Send in a single ephemeral message (short list); if too long, break into chunks
        out = "Configured stickies in this server:\n\n" + "\n\n".join(lines)
        # Discord message length safety — split if necessary
        if len(out) <= 1900:
            await ctx.send(out, ephemeral=False)
            return

        # Otherwise split into multiple messages (ephemeral)
        parts = []
        cur = "Configured stickies in this server:\n\n"
        for line in lines:
            if len(cur) + len(line) + 2 > 1900:
                parts.append(cur)
                cur = ""
            cur += line + "\n\n"
        if cur:
            parts.append(cur)

        for part in parts:
            await ctx.send(part, ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(StickyCog(bot))