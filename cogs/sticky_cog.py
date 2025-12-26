import asyncio
import json
from pathlib import Path
from typing import Dict, Any, Optional

import discord
from discord.ext import commands

# Try to reuse user's config.DATA_DIR if present
try:
    import config  # type: ignore
    DATA_DIR = Path(config.DATA_DIR)
except Exception:
    DATA_DIR = Path("data")

STICKY_FILE = DATA_DIR / "stickies.json"
DEFAULT_INTERVAL = 3  # default number of messages between reposts
EXCLUDED_CHANNELS = set()  # populate with channel IDs (ints or strings) to exclude

# Try to reuse theme utilities if present (optional)
try:
    from utils.theme import Colors, Emojis  # type: ignore
    HAS_THEME = True
except Exception:
    HAS_THEME = False

    class Emojis:
        SUCCESS = "✅"
        FAILURE = "❌"

    class Colors:
        PRIMARY = 0x5865F2  # kept for compatibility if referenced elsewhere

# Use normal Discord "blurple" purple for stickies
FALLBACK_PURPLE = 0x5865F2


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


class StickyCog(commands.Cog, name="Sticky Messages"):
    """
    Keeps a sticky message at the bottom of a channel by reposting it every N messages.
    Stored structure (stickies.json):
      {
        "<channel_id>": {
          "message": "Message content",
          "interval": 5,
          "last_message_id": 123456789012345678,
          "counter": 2
        },
        ...
      }
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        ensure_data_dir()
        self._lock = asyncio.Lock()
        self.stickies: Dict[str, Dict[str, Any]] = {}
        self._load_stickies()

    # ---------------------
    # Persistence
    # ---------------------
    def _load_stickies(self) -> None:
        try:
            if STICKY_FILE.exists():
                with STICKY_FILE.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
                    if isinstance(data, dict):
                        # Validate shape: ensure counters exist
                        for k, v in list(data.items()):
                            if not isinstance(v, dict):
                                continue
                            v.setdefault("interval", DEFAULT_INTERVAL)
                            v.setdefault("last_message_id", None)
                            # counter persisted here
                            v.setdefault("counter", 0)
                        self.stickies = data
                    else:
                        self.stickies = {}
            else:
                self.stickies = {}
        except (json.JSONDecodeError, OSError):
            # If file is corrupt or unreadable, start empty but don't crash
            self.stickies = {}

    def _save_stickies(self) -> None:
        try:
            STICKY_FILE.parent.mkdir(parents=True, exist_ok=True)
            with STICKY_FILE.open("w", encoding="utf-8") as fh:
                json.dump(self.stickies, fh, ensure_ascii=False, indent=2)
        except OSError:
            # best-effort; ignore write failures
            pass

    # ---------------------
    # Helpers
    # ---------------------
    def _channel_key(self, channel: discord.abc.Messageable) -> str:
        return str(getattr(channel, "id", channel))

    async def _delete_if_exists(self, channel: discord.TextChannel, msg_id: Optional[int]) -> None:
        if not msg_id:
            return
        try:
            msg = await channel.fetch_message(int(msg_id))
            await msg.delete()
        except discord.NotFound:
            return
        except discord.Forbidden:
            # Can't delete; just give up silently
            return
        except Exception:
            return

    def _get_interval(self, cfg: Dict[str, Any]) -> int:
        try:
            v = int(cfg.get("interval", DEFAULT_INTERVAL))
            return max(1, v)
        except Exception:
            return DEFAULT_INTERVAL

    async def _send_sticky(self, channel: discord.TextChannel, content: str) -> Optional[discord.Message]:
        """
        Send the sticky as an embed (normal purple). Return the sent message or None.
        """
        try:
            color = FALLBACK_PURPLE
            embed = discord.Embed(description=content or "(empty)", color=color)
            ## Removed the footer line and except after here
            return await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except discord.Forbidden:
            return None
        except Exception:
            return None

    def _get_success_emoji(self) -> str:
        # Prefer a bot-level override, then utils.theme.Emojis, then fallback
        try:
            return getattr(self.bot, "success_emoji", Emojis.SUCCESS)
        except Exception:
            return Emojis.SUCCESS

    # ---------------------
    # Lifecycle events
    # ---------------------
    @commands.Cog.listener()
    async def on_ready(self):
        # Validate stored last_message_id and reset if invalid (avoids stale ids preventing deletes)
        changed = False
        for ch_id, cfg in list(self.stickies.items()):
            last_id = cfg.get("last_message_id")
            if last_id:
                try:
                    ch = self.bot.get_channel(int(ch_id))
                    if isinstance(ch, discord.TextChannel):
                        await ch.fetch_message(int(last_id))
                    else:
                        cfg["last_message_id"] = None
                        changed = True
                except Exception:
                    # message not found or channel missing
                    cfg["last_message_id"] = None
                    changed = True
            # Ensure counter isn't larger than interval
            try:
                interval = self._get_interval(cfg)
                counter = int(cfg.get("counter", 0))
                if counter >= interval or counter < 0:
                    cfg["counter"] = 0
                    changed = True
            except Exception:
                cfg["counter"] = 0
                changed = True
        if changed:
            self._save_stickies()

    # ---------------------
    # Message handling
    # ---------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignore DMs, webhook messages, and messages from this bot itself.
        # We purposely do NOT ignore other bots so external bots (e.g. your rumble bot)
        # will advance the sticky counters.
        if message.guild is None or message.webhook_id is not None or message.author.id == getattr(self.bot, "user").id:
            return

        channel = message.channel
        ch_key = self._channel_key(channel)

        # Excluded channels
        if str(channel.id) in {str(x) for x in EXCLUDED_CHANNELS}:
            return

        # Nothing configured for this channel
        if ch_key not in self.stickies:
            return

        async with self._lock:
            cfg = self.stickies.get(ch_key)
            if not cfg:
                return

            interval = self._get_interval(cfg)
            # Use persisted counter
            counter = int(cfg.get("counter", 0))
            counter += 1
            # Persist the increment immediately so restarts don't lose progress
            cfg["counter"] = counter
            self._save_stickies()

            if counter < interval:
                return

            # Time to repost
            cfg["counter"] = 0
            # Persist reset right away
            self._save_stickies()

            # delete old sticky if exists
            last_id = cfg.get("last_message_id")
            if isinstance(channel, discord.TextChannel):
                await self._delete_if_exists(channel, last_id)
                sent = await self._send_sticky(channel, cfg.get("message", ""))
                if sent:
                    cfg["last_message_id"] = sent.id
                    self._save_stickies()
                else:
                    # couldn't send (permissions?) — disable sticky to avoid repeated failures
                    try:
                        del self.stickies[ch_key]
                        self._save_stickies()
                    except KeyError:
                        pass

    # ---------------------
    # Commands
    # ---------------------
    async def _ephemeral_reply(self, ctx: commands.Context, content: Optional[str] = None, *, embed: Optional[discord.Embed] = None) -> None:
        """
        Send an ephemeral reply if invoked as an interaction (slash), otherwise a normal reply.
        Accepts either content (str) and/or embed (discord.Embed).
        """
        try:
            if getattr(ctx, "interaction", None) is not None and getattr(ctx.interaction, "response", None) is not None:
                # If the interaction hasn't had a response yet, send ephemeral
                if not ctx.interaction.response.is_done():
                    await ctx.interaction.response.send_message(content=content, embed=embed, ephemeral=True)
                    return
            # Fallback to normal reply for prefix commands or already-responded interactions
            await ctx.reply(content, embed=embed, mention_author=False)
        except Exception:
            try:
                await ctx.reply(content, embed=embed, mention_author=False)
            except Exception:
                pass

    @commands.hybrid_command(name="stickplz", description="Set a sticky message for this channel.")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    async def sticky_set(self, ctx: commands.Context, interval: Optional[int] = None, *, message: str = ""):
        """
        Usage:
          /stickplz <interval> <message...>
        interval is optional (defaults to DEFAULT_INTERVAL).
        """
        if not message:
            await self._ephemeral_reply(ctx, f"{Emojis.FAILURE} You must provide the sticky message content.")
            return

        if interval is None:
            interval = DEFAULT_INTERVAL
        try:
            interval = max(1, int(interval))
        except Exception:
            interval = DEFAULT_INTERVAL

        ch_key = self._channel_key(ctx.channel)
        async with self._lock:
            self.stickies[ch_key] = {
                "message": message,
                "interval": interval,
                "last_message_id": None,
                "counter": 0,
            }
            self._save_stickies()

        await self._ephemeral_reply(ctx, f"{self._get_success_emoji()} Sticky set for this channel (every {interval} messages). Posting it now...")
        # Post immediately (delete any previous)
        try:
            await self.repost_now(ctx.channel)
        except Exception:
            pass

    async def repost_now(self, channel: discord.TextChannel) -> None:
        ch_key = self._channel_key(channel)
        async with self._lock:
            cfg = self.stickies.get(ch_key)
            if not cfg:
                return
            last_id = cfg.get("last_message_id")
            await self._delete_if_exists(channel, last_id)
            sent = await self._send_sticky(channel, cfg.get("message", ""))
            if sent:
                cfg["last_message_id"] = sent.id
                # reset counter on manual repost and persist
                cfg["counter"] = 0
                self._save_stickies()

    @commands.hybrid_command(name="byesticky", description="Disable the sticky in this channel.")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    async def sticky_remove(self, ctx: commands.Context):
        ch_key = self._channel_key(ctx.channel)
        async with self._lock:
            cfg = self.stickies.get(ch_key)
            if not cfg:
                await self._ephemeral_reply(ctx, f"{Emojis.FAILURE} There is no sticky configured for this channel.")
                return
            last_id = cfg.get("last_message_id")
            try:
                if isinstance(ctx.channel, discord.TextChannel):
                    await self._delete_if_exists(ctx.channel, last_id)
            except Exception:
                pass
            try:
                del self.stickies[ch_key]
            except KeyError:
                pass
            self._save_stickies()
        await self._ephemeral_reply(ctx, f"{self._get_success_emoji()} Sticky removed for this channel.")

    @commands.hybrid_command(name="stickies",
                             description="List active stickies (optionally for a specific channel).")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    async def sticky_list(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        """
        Usage:
          /stickies                 -> lists all active stickies in the guild (visible in invoking channel)
          /stickies channel:#test   -> list sticky for #test only

        Output: an embed listing channel, interval, counter and a preview of the sticky message.
        """
        async with self._lock:
            # choose keys to display
            if channel:
                ch_key = self._channel_key(channel)
                entries = {ch_key: self.stickies.get(ch_key)} if ch_key in self.stickies else {}
            else:
                # all stickies in memory
                entries = dict(self.stickies)

        if not entries:
            await self._ephemeral_reply(ctx,
                                        "No active stickies found." if not channel else f"No active sticky configured for {channel.mention}.")
            return

        # Build an embed
        embed = discord.Embed(title="Active Stickies", color=FALLBACK_PURPLE)

        # Limit preview length
        def _preview(text: str, length: int = 200) -> str:
            if not text:
                return "(empty)"
            t = text.strip().replace("\n", " ")
            return t if len(t) <= length else t[: length - 1].rstrip() + "…"

        added = 0
        for ch_key, cfg in entries.items():
            if not isinstance(cfg, dict):
                continue
            try:
                ch_id = int(ch_key)
            except Exception:
                ch_id = None
            if ch_id:
                ch_obj = self.bot.get_channel(ch_id)
                ch_repr = ch_obj.mention if isinstance(ch_obj, discord.TextChannel) else f"`{ch_key}`"
            else:
                ch_repr = f"`{ch_key}`"

            interval = self._get_interval(cfg)
            counter = int(cfg.get("counter", 0))
            last_id = cfg.get("last_message_id")
            preview = _preview(cfg.get("message", ""))
            field_value = f"Interval: {interval} msg(s)\nCounter: {counter}\nLast message id: `{last_id}`\nPreview: {preview}"
            embed.add_field(name=ch_repr, value=field_value, inline=False)
            added += 1
            if added >= 20:
                break

        total = len(entries)
        if total > added:
            embed.set_footer(text=f"Showing {added} of {total} stickies")

        await self._ephemeral_reply(ctx, embed=embed)

    def cog_unload(self):
        # save on unload (sync method is fine here)
        try:
            self._save_stickies()
        except Exception:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(StickyCog(bot))