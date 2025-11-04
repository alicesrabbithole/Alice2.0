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

    async def puzzle_autocomplete(self, interaction: discord.Interaction, current: str) -> List[
        app_commands.Choice[str]]:
        """Autocomplete for puzzle names, now safely using self.bot.data."""
        choices = []
        try:
            puzzles = self.bot.data.get("puzzles")
            if not isinstance(puzzles, dict):
                logger.warning("Autocomplete failed: self.bot.data['puzzles'] is not a dict or is missing.")
                return []
            if "all puzzles".startswith(current.lower()):
                choices.append(app_commands.Choice(name="All Puzzles (Random)", value="all_puzzles"))
            for slug, meta in puzzles.items():
                if not isinstance(slug, str) or not isinstance(meta, dict): continue
                display_name = meta.get("display_name", slug.replace("_", " ").title())
                if current.lower() in slug.lower() or current.lower() in display_name.lower():
                    choices.append(app_commands.Choice(name=display_name, value=slug))
        except Exception as e:
            logger.error(f"FATAL: Unhandled exception in puzzle_autocomplete: {e}")
            return []
        return choices[:25]

    async def _spawn_drop(self, channel: discord.TextChannel, puzzle_key: str, forced_piece: Optional[str] = None):
        display_name = get_puzzle_display_name(self.bot.data, puzzle_key)
        pieces_map = self.bot.data.get("pieces", {}).get(puzzle_key)
        if not pieces_map: return
        piece_id = forced_piece or random.choice(list(pieces_map.keys()))
        piece_path = pieces_map.get(piece_id)
        if not piece_path: return
        full_path = config.PUZZLES_ROOT / piece_path
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
        embed = discord.Embed(
            title=f"{emoji} A Wild Puzzle Piece Appears!",
            description=f"A piece of the **{display_name}** puzzle has dropped!\nClick the button to collect it.",
            color=Colors.THEME_COLOR
        ).set_image(url="attachment://puzzle_piece.png")
        raw_cfg = self.bot.data.get("drop_channels", {}).get(str(channel.id), {})
        claims_range = raw_cfg.get("claims_range", [1, 3])
        claim_limit = random.randint(claims_range[0], claims_range[1])
        view = DropView(self.bot, puzzle_key, display_name, piece_id, claim_limit)
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
            if raw_cfg.get("mode") != "timer": continue
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
                    if not channel: continue
                    puzzle_slug = raw_cfg.get("puzzle")
                    if not puzzle_slug: continue
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
        if message.author.bot or not message.guild: return
        raw_cfg = self.bot.data.get("drop_channels", {}).get(str(message.channel.id))
        if raw_cfg and raw_cfg.get("mode") == "messages":
            raw_cfg["message_count"] = raw_cfg.get("message_count", 0) + 1
            # --- THIS IS THE FIX ---
            # Correctly use 'next_trigger' which was accidentally removed.
            if raw_cfg["message_count"] >= raw_cfg.get("next_trigger", raw_cfg.get("value")):
                puzzle_key = resolve_puzzle_key(self.bot.data, raw_cfg.get("puzzle"))
                if puzzle_key:
                    await self._spawn_drop(message.channel, puzzle_key)
                    raw_cfg["message_count"] = 0
                save_data(self.bot.data)

    @commands.hybrid_command(name="spawndrop", description="Manually spawn a puzzle drop.")
    @app_commands.autocomplete(puzzle=puzzle_autocomplete)
    @is_admin()
    async def spawndrop(self, ctx: commands.Context, puzzle: str, channel: Optional[discord.TextChannel] = None):
        await ctx.defer(ephemeral=True)
        target_channel = channel or ctx.channel
        if puzzle == "all_puzzles":
            all_puzzles = list(self.bot.data.get("puzzles", {}).keys())
            if not all_puzzles: return await ctx.send("❌ No puzzles are available.", ephemeral=True)
            puzzle_key = random.choice(all_puzzles)
        else:
            puzzle_key = resolve_puzzle_key(self.bot.data, puzzle)
        if not puzzle_key: return await ctx.send(f"❌ Puzzle not found:
