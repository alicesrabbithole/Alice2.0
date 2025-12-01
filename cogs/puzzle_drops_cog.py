import discord
from discord.ext import commands, tasks
from discord import app_commands
import random
import io
import logging
from typing import Optional, List
from datetime import datetime, timezone, timedelta
from PIL import Image

import config
from utils.db_utils import (
    save_data,
    resolve_puzzle_key,
    get_puzzle_display_name,
)
from utils.log_utils import log
from ui.views import DropView
from utils.checks import is_admin
from utils.theme import THEMES, PUZZLE_CONFIG, Emojis, Colors # Make sure Emojis and Colors are imported

logger = logging.getLogger(__name__)

FREQUENCY_COMBINED_RANGES = {
    "high": {"time": (2 * 60, 7 * 60), "messages": (7, 15)},
    "medium": {"time": (10 * 60, 15 * 60), "messages": (30, 50)},
    "low": {"time": (16 * 60, 30 * 60), "messages": (60, 90)},
}

class PuzzleDropsCog(commands.Cog, name="Puzzle Drops"):
    """Manages the automatic and manual dropping of puzzle pieces."""

    DEFAULT_DROP_MODE = "timer"
    DEFAULT_DROP_TIMER_MINUTES = 45
    DEFAULT_DROP_MESSAGE_COUNT = 100

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.drop_scheduler.start()

    def cog_unload(self):
        self.drop_scheduler.cancel()

    async def puzzle_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        choices = []
        try:
            puzzles = self.bot.data.get("puzzles")
            if not isinstance(puzzles, dict):
                logger.warning("Autocomplete failed: self.bot.data['puzzles'] is not a dict or is missing.")
                return []
            if "all puzzles".startswith(current.lower()):
                choices.append(app_commands.Choice(name="All Puzzles (Random)", value="all_puzzles"))
            for slug, meta in puzzles.items():
                if not isinstance(slug, str) or not isinstance(meta, dict):
                    continue
                display_name = meta.get("display_name", slug.replace("_", " ").title())
                if current.lower() in slug.lower() or current.lower() in display_name.lower():
                    choices.append(app_commands.Choice(name=display_name, value=slug))
        except Exception as e:
            logger.error(f"FATAL: Unhandled exception in puzzle_autocomplete: {e}")
            return []
        return choices[:25]

    async def _spawn_drop(
        self, channel: discord.TextChannel, puzzle_key: str, forced_piece: Optional[str] = None
    ):
        # Pull config and theme info
        meta = PUZZLE_CONFIG.get(puzzle_key, {})
        theme_name = meta.get("theme")
        theme = THEMES.get(theme_name) if theme_name else None

        display_name = meta.get("display_name", get_puzzle_display_name(self.bot.data, puzzle_key))
        pieces_map = self.bot.data.get("pieces", {}).get(puzzle_key)
        if not pieces_map:
            logger.warning(f"No pieces found for puzzle {puzzle_key}")
            return

        piece_id = forced_piece or random.choice(list(pieces_map.keys()))
        piece_path = pieces_map.get(piece_id)
        if not piece_path:
            logger.warning(f"Piece {piece_id} not found in map for puzzle {puzzle_key}")
            return

        full_path = config.PUZZLES_ROOT / piece_path
        logger.debug(f"Attempting to open piece {piece_id} at {full_path}")

        try:
            with Image.open(full_path) as img:
                img.thumbnail((128, 128))
                buffer = io.BytesIO()
                img.save(buffer, "PNG")
                buffer.seek(0)
                file = discord.File(buffer, filename="puzzle_piece.png")
        except Exception as e:
            logger.exception(f"Failed to process image for drop, using raw file: {e}")
            file = discord.File(full_path, filename="puzzle_piece.png")

        ping_role_id = self.bot.data.get("piece_drop_ping_role_id")
        ping_out = f"<@&{ping_role_id}>" if ping_role_id else None

        emoji = theme.emoji if theme else (getattr(config, "CUSTOM_EMOJI_STRING", Emojis.PUZZLE_PIECE))
        embed_color = theme.color if theme else Colors.THEME_COLOR
        button_color = theme.button_color if theme else Colors.THEME_COLOR

        embed = (
            discord.Embed(
                title=f"{emoji} A Wild Puzzle Piece Appears!",
                description=f"A piece of the **{display_name}** puzzle has dropped!\nClick the button to collect it.",
                color=embed_color,
            )
            .set_image(url="attachment://puzzle_piece.png")
        )

        raw_cfg = self.bot.data.get("drop_channels", {}).get(str(channel.id), {})
        claims_range = raw_cfg.get("claims_range", [1, 3])
        claim_limit = random.randint(claims_range[0], claims_range[1])
        view = DropView(self.bot, puzzle_key, display_name, piece_id, claim_limit, button_color=button_color)

        try:
            message = await channel.send(
                content=ping_out,
                embed=embed,
                file=file,
                view=view
            )
            view.message = message
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.exception(f"Failed to send drop in #{channel.name}: {e}")

    @tasks.loop(seconds=30)
    async def drop_scheduler(self):
        await self.bot.wait_until_ready()
        now = datetime.now(timezone.utc)
        drop_channels = self.bot.data.get("drop_channels", {})
        data_changed = False

        for ch_id_str, raw_cfg in drop_channels.items():
            mode = raw_cfg.get("mode")
            channel = self.bot.get_channel(int(ch_id_str))
            if not channel:
                continue

            # --- Frequency random mode ---
            if mode == "random" and raw_cfg.get("trigger") == "frequency":
                freq_mode = raw_cfg.get("frequency_mode", "medium")
                ranges = FREQUENCY_COMBINED_RANGES.get(freq_mode, FREQUENCY_COMBINED_RANGES["medium"])
                min_secs, max_secs = ranges["time"]
                min_msgs, max_msgs = ranges["messages"]

                if "next_trigger_time" not in raw_cfg:
                    raw_cfg["next_trigger_time"] = random.randint(min_secs, max_secs)
                    data_changed = True
                if "next_trigger_messages" not in raw_cfg:
                    raw_cfg["next_trigger_messages"] = random.randint(min_msgs, max_msgs)
                    data_changed = True
                if "message_count" not in raw_cfg:
                    raw_cfg["message_count"] = 0
                    data_changed = True
                last_drop_str = raw_cfg.get("last_drop_time")
                if not last_drop_str:
                    raw_cfg["last_drop_time"] = now.isoformat()
                    data_changed = True
                    continue
                last_drop_time = datetime.fromisoformat(last_drop_str)
                time_ready = now >= last_drop_time + timedelta(seconds=raw_cfg["next_trigger_time"])
                if time_ready and raw_cfg["message_count"] < raw_cfg["next_trigger_messages"]:
                    all_puzzles = list(self.bot.data.get("puzzles", {}).keys())
                    puzzle_key = random.choice(all_puzzles) if all_puzzles else None
                    if puzzle_key:
                        await self._spawn_drop(channel, puzzle_key)
                        raw_cfg["last_drop_time"] = now.isoformat()
                        raw_cfg["next_trigger_time"] = random.randint(min_secs, max_secs)
                        raw_cfg["next_trigger_messages"] = random.randint(min_msgs, max_msgs)
                        raw_cfg["message_count"] = 0
                        data_changed = True
                    continue

            # --- Old timer mode ---
            elif mode == "timer":
                last_drop_str = raw_cfg.get("last_drop_time")
                if not last_drop_str:
                    raw_cfg["last_drop_time"] = now.isoformat()
                    data_changed = True
                    continue
                last_drop_time = datetime.fromisoformat(last_drop_str)
                seconds_to_wait = raw_cfg.get("value", 3600)
                if now >= last_drop_time + timedelta(seconds=seconds_to_wait):
                    puzzle_slug = raw_cfg.get("puzzle")
                    if not puzzle_slug:
                        continue
                    if puzzle_slug == "all_puzzles":
                        all_puzzles = list(self.bot.data.get("puzzles", {}).keys())
                        puzzle_key = random.choice(all_puzzles) if all_puzzles else None
                    else:
                        puzzle_key = resolve_puzzle_key(self.bot.data, puzzle_slug)
                    if puzzle_key:
                        await self._spawn_drop(channel, puzzle_key)
                        raw_cfg["last_drop_time"] = now.isoformat()
                        data_changed = True

        if data_changed:
            save_data(self.bot.data)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        raw_cfg = self.bot.data.get("drop_channels", {}).get(str(message.channel.id))
        if not raw_cfg:
            return

        # --- Frequency random mode (message-based) ---
        if raw_cfg.get("mode") == "random" and raw_cfg.get("trigger") == "frequency":
            freq_mode = raw_cfg.get("frequency_mode", "medium")
            ranges = FREQUENCY_COMBINED_RANGES.get(freq_mode, FREQUENCY_COMBINED_RANGES["medium"])
            min_secs, max_secs = ranges["time"]
            min_msgs, max_msgs = ranges["messages"]
            raw_cfg["message_count"] = raw_cfg.get("message_count", 0) + 1
            next_msgs = raw_cfg.get("next_trigger_messages", random.randint(min_msgs, max_msgs))
            now = datetime.now(timezone.utc)
            last_drop_str = raw_cfg.get("last_drop_time")
            if last_drop_str:
                last_drop_time = datetime.fromisoformat(last_drop_str)
                time_ready = now >= last_drop_time + timedelta(
                    seconds=raw_cfg.get("next_trigger_time", random.randint(min_secs, max_secs)))
            else:
                raw_cfg["last_drop_time"] = now.isoformat()
                return
            if raw_cfg["message_count"] >= next_msgs and not time_ready:
                all_puzzles = list(self.bot.data.get("puzzles", {}).keys())
                puzzle_key = random.choice(all_puzzles) if all_puzzles else None
                if puzzle_key:
                    await self._spawn_drop(message.channel, puzzle_key)
                    raw_cfg["last_drop_time"] = now.isoformat()
                    raw_cfg["next_trigger_time"] = random.randint(min_secs, max_secs)
                    raw_cfg["next_trigger_messages"] = random.randint(min_msgs, max_msgs)
                    raw_cfg["message_count"] = 0
                    save_data(self.bot.data)
            return

        # --- Old messages mode ---
        if raw_cfg.get("mode") == "messages":
            raw_cfg["message_count"] = raw_cfg.get("message_count", 0) + 1
            if raw_cfg["message_count"] >= raw_cfg.get("next_trigger", raw_cfg.get("value")):
                puzzle_slug = raw_cfg.get("puzzle")
                if puzzle_slug == "all_puzzles":
                    all_puzzles = list(self.bot.data.get("puzzles", {}).keys())
                    puzzle_key = random.choice(all_puzzles) if all_puzzles else None
                else:
                    puzzle_key = resolve_puzzle_key(self.bot.data, puzzle_slug)
                if puzzle_key:
                    await self._spawn_drop(message.channel, puzzle_key)
                    raw_cfg["message_count"] = 0
                save_data(self.bot.data)

    @commands.hybrid_command(
        name="spawndrop",
        description="Manually spawn a puzzle drop (optionally for a specific piece)."
    )
    @app_commands.autocomplete(puzzle=puzzle_autocomplete)
    @is_admin()
    async def spawndrop(
        self,
        ctx: commands.Context,
        puzzle: str,
        channel: Optional[discord.TextChannel] = None,
        piece: Optional[str] = None
    ):
        await ctx.defer(ephemeral=True)
        target_channel = channel or ctx.channel
        if puzzle == "all_puzzles":
            all_puzzles = list(self.bot.data.get("puzzles", {}).keys())
            if not all_puzzles:
                return await ctx.send("❌ No puzzles are available.", ephemeral=True)
            puzzle_key = random.choice(all_puzzles)
        else:
            puzzle_key = resolve_puzzle_key(self.bot.data, puzzle)
        if not puzzle_key:
            return await ctx.send(f"❌ Puzzle not found: `{puzzle}`", ephemeral=True)

        piece_id = None
        if piece is not None:
            pieces_map = self.bot.data.get("pieces", {}).get(puzzle_key)
            if not pieces_map or piece not in pieces_map:
                return await ctx.send(f"❌ Piece `{piece}` is not valid for puzzle `{puzzle}`.", ephemeral=True)
            piece_id = piece

        await self._spawn_drop(target_channel, puzzle_key, forced_piece=piece_id)
        display_name = get_puzzle_display_name(self.bot.data, puzzle_key)
        await ctx.send(
            f"✅ Drop for **{display_name}**{' (piece `' + piece + '`)' if piece else ''} spawned in {target_channel.mention}.",
            ephemeral=True
        )

    @commands.hybrid_command(
        name="pingset_drops",
        description="Set the role to ping on every puzzle piece drop."
    )
    @is_admin()
    async def pingset_drops(self, ctx: commands.Context, role: discord.Role):
        self.bot.data["piece_drop_ping_role_id"] = role.id
        save_data(self.bot.data)
        await ctx.send(f"🛎️ Drop ping role has been set to {role.mention}. Future drops will ping this role.", ephemeral=False)

    @commands.hybrid_command(name="setdropchannel", description="Configure a channel for automatic puzzle drops.")
    @app_commands.autocomplete(puzzle=puzzle_autocomplete)
    @is_admin()
    async def setdropchannel(
            self,
            ctx: commands.Context,
            channel: discord.TextChannel,
            puzzle: str,
            mode: Optional[str] = None,
            value: Optional[int] = None,
            frequency_mode: Optional[str] = None  # Add this argument!
    ):
        await ctx.defer(ephemeral=False)
        drop_channels = self.bot.data.setdefault("drop_channels", {})
        display_name = get_puzzle_display_name(self.bot.data, puzzle) if puzzle != "all_puzzles" else "All Puzzles"

        final_mode = mode or self.DEFAULT_DROP_MODE

        # --- Old modes ---
        if final_mode == "timer":
            final_value = (value * 60) if value is not None else (self.DEFAULT_DROP_TIMER_MINUTES * 60)
            drop_channels[str(channel.id)] = {
                "puzzle": puzzle,
                "mode": "timer",
                "value": final_value,
                "last_drop_time": datetime.now(timezone.utc).isoformat(),
            }
            summary = f"every {final_value // 60} minutes"
        elif final_mode == "messages":
            final_value = value if value is not None else self.DEFAULT_DROP_MESSAGE_COUNT
            drop_channels[str(channel.id)] = {
                "puzzle": puzzle,
                "mode": "messages",
                "value": final_value,
                "message_count": 0,
                "next_trigger": final_value
            }
            summary = f"every {final_value} messages"
        elif final_mode == "random" or (final_mode == "frequency" and frequency_mode in FREQUENCY_COMBINED_RANGES):
            freq_mode = frequency_mode or "medium"
            ranges = FREQUENCY_COMBINED_RANGES.get(freq_mode, FREQUENCY_COMBINED_RANGES["medium"])
            min_secs, max_secs = ranges["time"]
            min_msgs, max_msgs = ranges["messages"]
            drop_channels[str(channel.id)] = {
                "puzzle": "all_puzzles",
                "mode": "random",
                "trigger": "frequency",
                "frequency_mode": freq_mode,
                "last_drop_time": datetime.now(timezone.utc).isoformat(),
                "next_trigger_time": random.randint(min_secs, max_secs),
                "next_trigger_messages": random.randint(min_msgs, max_msgs),
                "message_count": 0,
            }
            summary = f"random every {min_secs // 60}-{max_secs // 60} minutes OR {min_msgs}-{max_msgs} messages"
        else:
            return await ctx.send("❌ Invalid mode. Use 'timer', 'messages', or 'random'/frequency.", ephemeral=True)

        save_data(self.bot.data)
        await ctx.send(
            f"✅ Drops for **{display_name}** configured in {channel.mention}.\n"
            f"Mode: `{final_mode}` | Trigger: `{summary}`.",
            ephemeral=False
        )
        await log(
            self.bot,
            f"🔧 Drop channel configured for **{display_name}** in `#{channel.name}` by `{ctx.author}`.")

    @commands.hybrid_command(name="removedropchannel", description="Remove a channel from automatic puzzle drops.")
    @is_admin()
    async def removedropchannel(self, ctx: commands.Context, channel: discord.TextChannel):
        drop_channels = self.bot.data.get("drop_channels", {})
        if str(channel.id) in drop_channels:
            drop_channels.pop(str(channel.id))
            save_data(self.bot.data)
            await ctx.send(f"❌ Drop channel removed: {channel.mention}", ephemeral=False)
            await log(
                self.bot,
                f"🔧 Drop channel removed for `#{channel.name}` by `{ctx.author}`."
            )
        else:
            await ctx.send(f"Channel {channel.mention} is not configured for drops.", ephemeral=True)

# --- Cog entry point ---
async def setup(bot: commands.Bot):
    await bot.add_cog(PuzzleDropsCog(bot))