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
    get_puzzle_display_name
)
from utils.log_utils import log
from ui.views import DropView
from utils.checks import is_admin
from utils.theme import Colors

logger = logging.getLogger(__name__)

class PuzzleDropsCog(commands.Cog, name="Puzzle Drops"):
    """Manages the automatic and manual dropping of puzzle pieces."""

    DEFAULT_DROP_MODE = "timer"
    DEFAULT_DROP_TIMER_MINUTES = 60
    DEFAULT_DROP_MESSAGE_COUNT = 100

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.drop_scheduler.start()

    def cog_unload(self):
        self.drop_scheduler.cancel()

    async def puzzle_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
        """Autocomplete for puzzle names."""
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
        display_name = get_puzzle_display_name(self.bot.data, puzzle_key)
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

        emoji = config.CUSTOM_EMOJI_STRING or config.DEFAULT_EMOJI
        embed = (
            discord.Embed(
                title=f"{emoji} A Wild Puzzle Piece Appears!",
                description=f"A piece of the **{display_name}** puzzle has dropped!\nClick the button to collect it.",
                color=Colors.THEME_COLOR,
            )
            .set_image(url="attachment://puzzle_piece.png")
        )

        raw_cfg = self.bot.data.get("drop_channels", {}).get(str(channel.id), {})
        claims_range = raw_cfg.get("claims_range", [1, 3])
        claim_limit = random.randint(claims_range[0], claims_range[1])

        # get user_pieces dict from bot.data for this drop
        user_pieces = self.bot.data.get("user_pieces", {})

        view = DropView(self.bot, puzzle_key, display_name, piece_id, claim_limit, user_pieces)

        try:
            message = await channel.send(embed=embed, file=file, view=view)
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
            if raw_cfg.get("mode") != "timer":
                continue
            last_drop_str = raw_cfg.get("last_drop_time")
            if not last_drop_str:
                raw_cfg["last_drop_time"] = now.isoformat()
                data_changed = True
                continue
            try:
                last_drop_time = datetime.fromisoformat(last_drop_str)
                seconds_to_wait = raw_cfg.get("value", 3600)
                if now >= last_drop_time + timedelta(seconds=seconds_to_wait):
                    channel = self.bot.get_channel(int(ch_id_str))
                    if not channel:
                        continue
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
            except (ValueError, TypeError) as e:
                logger.error(f"Could not process drop channel {ch_id_str}: {e}")
                continue

        if data_changed:
            save_data(self.bot.data)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        raw_cfg = self.bot.data.get("drop_channels", {}).get(str(message.channel.id))
        if raw_cfg and raw_cfg.get("mode") == "messages":
            raw_cfg["message_count"] = raw_cfg.get("message_count", 0) + 1
            if raw_cfg["message_count"] >= raw_cfg.get("next_trigger", raw_cfg.get("value")):
                puzzle_key = resolve_puzzle_key(self.bot.data, raw_cfg.get("puzzle"))
                if puzzle_key:
                    await self._spawn_drop(message.channel, puzzle_key)
                    raw_cfg["message_count"] = 0
                save_data(self.bot.data)

    @commands.hybrid_command(name="spawndrop", description="Manually spawn a puzzle drop.")
    @app_commands.autocomplete(puzzle=puzzle_autocomplete)
    @is_admin()
    async def spawndrop(
        self, ctx: commands.Context, puzzle: str, channel: Optional[discord.TextChannel] = None
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

        await self._spawn_drop(target_channel, puzzle_key)
        display_name = get_puzzle_display_name(self.bot.data, puzzle_key)
        await ctx.send(f"✅ Drop for **{display_name}** spawned in {target_channel.mention}.", ephemeral=True)

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
    ):
        await ctx.defer(ephemeral=False)
        if puzzle != "all_puzzles":
            puzzle_key = resolve_puzzle_key(self.bot.data, puzzle)
            if not puzzle_key:
                return await ctx.send(f"❌ Puzzle not found: `{puzzle}`", ephemeral=False)

        final_mode = mode or self.DEFAULT_DROP_MODE
        if final_mode == "timer":
            final_value = (value * 60) if value is not None else (self.DEFAULT_DROP_TIMER_MINUTES * 60)
            value_display = value if value is not None else self.DEFAULT_DROP_TIMER_MINUTES
            unit_display = "minutes"
        elif final_mode == "messages":
            final_value = value if value is not None else self.DEFAULT_DROP_MESSAGE_COUNT
            value_display = final_value
            unit_display = "messages"
        else:
            return await ctx.send(f"❌ Invalid mode `{final_mode}`. Use 'timer' or 'messages'.", ephemeral=True)

        drop_channels = self.bot.data.setdefault("drop_channels", {})
        drop_channels[str(channel.id)] = {
            "puzzle": puzzle,
            "mode": final_mode,
            "value": final_value,
            "last_drop_time": datetime.now(timezone.utc).isoformat(),
            "message_count": 0,
            "next_trigger": final_value if final_mode == "messages" else 0
        }
        save_data(self.bot.data)

        display_name = (
            get_puzzle_display_name(self.bot.data, puzzle)
            if puzzle != "all_puzzles"
            else "All Puzzles"
        )
        await ctx.send(
            f"✅ Drops for **{display_name}** are now configured in {channel.mention}.\n"
            f"Mode: `{final_mode}` | Every: `{value_display} {unit_display}`.",
            ephemeral=False
        )
        await log(
            self.bot,
            f"🔧 Drop channel configured for **{display_name}** in `#{channel.name}` by `{ctx.author}`."
        )

    @commands.hybrid_command(name="remove_drop_channel", description="Remove a channel from the drop list.")
    @is_admin()
    async def remove_drop_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Remove a channel from drop channels."""
        data = self.bot.data
        removed = data.setdefault("drop_channels", {}).pop(str(channel.id), None)
        save_data(self.bot.data)
        if removed:
            await ctx.send(f"✅ Removed drop from {channel.mention}.", ephemeral=True)
        else:
            await ctx.send(f"❌ {channel.mention} wasn’t set up for drops.", ephemeral=True)

    @commands.hybrid_command(name="list_drop_settings", description="List all current drop channel settings.")
    @is_admin()
    async def list_drop_settings(self, ctx: commands.Context):
        """List all current drop channel settings."""
        data = self.bot.data
        drop_channels = data.get("drop_channels", {})
        if not drop_channels:
            await ctx.send("No drop channels configured.", ephemeral=True)
            return
        embed = discord.Embed(title="Active Drop Channels")
        for cid, info in drop_channels.items():
            chan = ctx.guild.get_channel(int(cid))
            channel_name = chan.mention if chan else f"ID {cid}"
            embed.add_field(
                name=channel_name,
                value=f"Puzzle: {info.get('puzzle')}\nMode: {info.get('mode')}\nValue: {info.get('value')}\nNext Trigger: {info.get('next_trigger')}",
                inline=False
            )
        await ctx.send(embed=embed, ephemeral=True)

# --- Cog entry point ---
async def setup(bot: commands.Bot):
    await bot.add_cog(PuzzleDropsCog(bot))