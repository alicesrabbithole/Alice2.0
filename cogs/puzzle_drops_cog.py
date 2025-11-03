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
    save_data, resolve_puzzle_key, get_puzzle_display_name
)
from utils.log_utils import log
from .ui.views import DropView
from utils.checks import is_admin
from utils.theme import Colors

logger = logging.getLogger(__name__)


class PuzzleDropsCog(commands.Cog, name="Puzzle Drops"):
    """Manages the automatic and manual dropping of puzzle pieces."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.drop_scheduler.start()

    def cog_unload(self):
        self.drop_scheduler.cancel()

    async def puzzle_autocomplete(self, interaction: discord.Interaction, current: str) -> List[
        app_commands.Choice[str]]:
        """Autocomplete for puzzle names, including a random option."""
        puzzles = self.bot.data.get("puzzles", {})
        choices = []
        # Add "All Puzzles" option
        if "all puzzles".startswith(current.lower()):
            choices.append(app_commands.Choice(name="All Puzzles (Random)", value="all_puzzles"))

        # Add individual puzzles
        for slug, _ in puzzles.items():
            display_name = get_puzzle_display_name(self.bot.data, slug)
            if current.lower() in slug.lower() or current.lower() in display_name.lower():
                choices.append(app_commands.Choice(name=display_name, value=slug))
        return choices[:25]

    async def _spawn_drop(self, channel: discord.TextChannel, puzzle_key: str, forced_piece: Optional[str] = None):
        """Internal logic to create and send a puzzle drop."""
        display_name = get_puzzle_display_name(self.bot.data, puzzle_key)
        pieces_map = self.bot.data.get("pieces", {}).get(puzzle_key)
        if not pieces_map:
            logger.warning(f"Attempted to spawn drop for '{puzzle_key}' but no pieces were found.")
            return

        piece_id = forced_piece or random.choice(list(pieces_map.keys()))
        piece_path = pieces_map.get(piece_id)
        if not piece_path:
            logger.warning(f"Could not find path for piece '{piece_id}' in puzzle '{puzzle_key}'.")
            return

        try:
            with Image.open(piece_path) as img:
                img.thumbnail((128, 128))
                buffer = io.BytesIO()
                img.save(buffer, "PNG")
                buffer.seek(0)
                file = discord.File(buffer, filename="puzzle_piece.png")
        except Exception as e:
            logger.exception(f"Failed to process image for drop. Using raw file. Error: {e}")
            file = discord.File(piece_path, filename="puzzle_piece.png")

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
        """The main task loop that checks if it's time to drop a puzzle piece."""
        await self.bot.wait_until_ready()
        now = datetime.now(timezone.utc)
        drop_channels = self.bot.data.get("drop_channels", {})

        for ch_id_str, raw_cfg in drop_channels.items():
            if raw_cfg.get("mode") != "timer":
                continue

            last_drop_str = raw_cfg.get("last_drop_time")
            if not last_drop_str:
                raw_cfg["last_drop_time"] = now.isoformat()
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
            except (ValueError, TypeError) as e:
                logger.error(f"Could not process drop channel {ch_id_str} due to invalid data: {e}. Skipping.")
                continue

        # Save data once at the end of the loop
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
                    # Reset the trigger for the next drop
                    raw_cfg["next_trigger"] = raw_cfg["message_count"] + raw_cfg.get("value")
                save_data(self.bot.data)

        # IMPORTANT: This allows the bot to process commands in messages.
        await self.bot.process_commands(message)

    @commands.hybrid_command(name="spawndrop", description="Manually spawn a puzzle drop.")
    @app_commands.autocomplete(puzzle=puzzle_autocomplete)
    @is_admin()
    async def spawndrop(self, ctx: commands.Context, puzzle: str, channel: Optional[discord.TextChannel] = None):
        """Manually spawns a puzzle piece drop."""
        await ctx.defer(ephemeral=True)
        target_channel = channel or ctx.channel

        if puzzle == "all_puzzles":
            all_puzzles = list(self.bot.data.get("puzzles", {}).keys())
            if not all_puzzles:
                return await ctx.send("❌ No puzzles are available to choose from.", ephemeral=True)
            puzzle_key = random.choice(all_puzzles)
        else:
            puzzle_key = resolve_puzzle_key(self.bot.data, puzzle)

        if not puzzle_key:
            return await ctx.send(f"❌ Puzzle not found: `{puzzle}`", ephemeral=True)

        await self._spawn_drop(target_channel, puzzle_key)
        display_name = get_puzzle_display_name(self.bot.data, puzzle_key)
        await ctx.send(f"✅ Drop for **{display_name}** has been spawned in {target_channel.mention}.",
                       ephemeral=True)

    @commands.hybrid_command(name="setdropchannel", description="Configure a channel for automatic puzzle drops.")
    @app_commands.autocomplete(puzzle=puzzle_autocomplete)
    @is_admin()
    async def setdropchannel(self, ctx: commands.Context, channel: discord.TextChannel, puzzle: str,
                             mode: str, value: int):  # <<< FIX IS HERE
        """Sets up a channel for automatic drops (timer or message-based)."""
        await ctx.defer(ephemeral=False)

        if puzzle != "all_puzzles":
            puzzle_key = resolve_puzzle_key(self.bot.data, puzzle)
            if not puzzle_key:
                return await ctx.send(f"❌ Puzzle not found: `{puzzle}`", ephemeral=False)

        final_value = value * 60 if mode == "timer" else value
        drop_channels = self.bot.data.setdefault("drop_channels", {})
        drop_channels[str(channel.id)] = {
            "puzzle": puzzle,
            "mode": mode,
            "value": final_value,
            "last_drop_time": datetime.now(timezone.utc).isoformat(),
            "message_count": 0,
            "next_trigger": final_value
        }
        save_data(self.bot.data)
        display_name = get_puzzle_display_name(self.bot.data, puzzle) if puzzle != "all_puzzles" else "All Puzzles"
        await ctx.send(f"✅ Drops for **{display_name}** are now configured in {channel.mention}.", ephemeral=False)
        await log(self.bot, f"🔧 Drop channel configured for **{display_name}** in `#{channel.name}` by `{ctx.author}`.")

    @setdropchannel.autocomplete("mode")
    async def mode_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        modes = ["timer", "messages"]
        return [app_commands.Choice(name=mode.title(), value=mode) for mode in modes if current.lower() in mode.lower()]


async def setup(bot: commands.Bot):
    await bot.add_cog(PuzzleDropsCog(bot))