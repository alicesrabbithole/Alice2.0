import asyncio
import discord
from discord.ext import commands
import json
import os
import tempfile
import logging
from typing import Dict

from utils.checks import is_staff
from utils.theme import Colors, Emojis
import config

logger = logging.getLogger(__name__)

STICKY_DATA_FILE = config.DATA_DIR / "stickies.json"
MESSAGE_THRESHOLD = 5

# Set of excluded channel IDs (as strings) where stickies will NOT work.
EXCLUDED_CHANNELS = set()

class StickyCog(commands.Cog, name="Sticky Messages"):
    """Manages sticky messages that repost after a set number of messages."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.stickies: Dict[str, Dict] = {}
        self.message_counts: Dict[str, int] = {}
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

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Only check channels that are not excluded, and ignore DMs/bots
        if message.guild is None or message.author.bot:
            return

        channel_id = str(message.channel.id)
        if channel_id in EXCLUDED_CHANNELS:
            return

        if channel_id in self.stickies:
            # If sticky is disabled, do nothing
            sticky_info = self.stickies.get(channel_id, {})
            if sticky_info.get("disabled"):
                return

            self.message_counts[channel_id] = self.message_counts.get(channel_id, 0) + 1
            if self.message_counts[channel_id] >= MESSAGE_THRESHOLD:
                self.message_counts[channel_id] = 0
                await self.repost_sticky(message.channel)

    async def repost_sticky(self, channel: discord.TextChannel):
        channel_id = str(channel.id)
        sticky_info = self.stickies.get(channel_id)
        if not sticky_info:
            return

        last_message_id = sticky_info.get("last_message_id")
        if last_message_id:
            try:
                old_message = await channel.fetch_message(last_message_id)
                # only try to delete our bot's message
                if old_message and old_message.author and old_message.author.id == self.bot.user.id:
                    await old_message.delete()
            except discord.NotFound:
                # message already gone; no action
                pass
            except discord.Forbidden:
                logger.warning("No permission to delete old sticky in channel %s; marking sticky disabled", channel_id)
                sticky_info["disabled"] = True
                self.save_stickies()
                return
            except Exception:
                logger.exception("Unexpected error fetching/deleting old sticky in %s", channel_id)

        embed = discord.Embed(description=sticky_info.get("message", ""), color=Colors.PRIMARY)
        try:
            new_message = await channel.send(embed=embed)
            # store bot message id so we can remove it next time
            self.stickies[channel_id]["last_message_id"] = new_message.id
            self.save_stickies()
            logger.info("Posted sticky in channel %s (msg=%s)", channel_id, new_message.id)

            # Best-effort pin the sticky so it is less likely to be removed
            try:
                await new_message.pin(reason="Sticky message pinned by bot")
            except Exception:
                logger.debug("Could not pin sticky message; missing Manage Messages or other error", exc_info=True)

        except discord.Forbidden:
            logger.warning("No permission to send sticky in channel %s; marking sticky disabled", channel_id)
            sticky_info["disabled"] = True
            self.save_stickies()
        except Exception:
            logger.exception("Failed to post sticky in channel %s", channel_id)

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
            if not sticky or not sticky.get("last_message_id") or sticky.get("disabled"):
                return
            # If their deletion matches our stored message id, repost right away
            if int(sticky["last_message_id"]) == int(msg_id):
                channel = self.bot.get_channel(int(channel_id))
                if not channel:
                    try:
                        channel = await self.bot.fetch_channel(int(channel_id))
                    except Exception:
                        logger.exception("Could not fetch channel %s to repost sticky", channel_id)
                        return
                # Small delay to avoid race with delete triggers
                await asyncio.sleep(0.2)
                await self.repost_sticky(channel)
        except Exception:
            logger.exception("Error in on_raw_message_delete handler", exc_info=True)

    # --- User Commands ---
    @commands.hybrid_command(name="stickplz", description="Add or update a sticky.")
    @commands.guild_only()
    @is_staff()
    async def sticky_set(self, ctx: commands.Context, *, message: str):
        channel_id = str(ctx.channel.id)
        self.stickies[channel_id] = {"message": message, "last_message_id": None, "disabled": False}
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

async def setup(bot: commands.Bot):
    await bot.add_cog(StickyCog(bot))