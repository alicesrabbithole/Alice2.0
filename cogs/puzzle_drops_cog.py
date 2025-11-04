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
    load_data,
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

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.drop_scheduler.start()

    def cog_unload(self):
        self.drop_scheduler.cancel()

    async def puzzle_autocomplete(self, interaction: discord.Interaction, current: str) -> List[
        app_commands.Choice[str]]:
        """Autocomplete for puzzle names, including a random option."""
        # --- THIS IS THE FINAL, ROBUST FIX ---
        choices = []
        try:
            bot_data = load_data()
            puzzles = bot_data.get("puzzles")

            # Safety check: If puzzles data is missing or not a dict, return empty list.
            if not isinstance(puzzles, dict):
                return []

            # Add "All Puzzles" option
            if "all puzzles".startswith(current.lower()):
                choices.append(app_commands.Choice(name="All Puzzles (Random)", value="all_puzzles"))

            # Add individual puzzles
            for slug, meta in puzzles.items():
                # Safety check: ensure slug is a string and meta is a dictionary
                if not isinstance(slug, str) or not isinstance(meta, dict):
                    continue

                display_name = meta.get("display_name", slug.replace("_", " ").title())
                if current.lower() in slug.lower() or current.lower() in display_name.lower():
                    choices.append(app_commands.Choice(name=display_name, value=slug))

        except Exception as e:
            logger.error(f"FATAL: Unhandled exception in puzzle_autocomplete: {e}")
            # Return an empty list on any unexpected failure.
            return []

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

        # Construct the full, absolute path to the image file.
        full_path = config.PUZZLES_ROOT / piece_path

        try:
            with Image.open(full_path) as img:
                img.thumbnail((128, 128))
                buffer = io.BytesIO()
                img.save(buffer, "PNG")
                buffer.seek(0)
                file = discord.File(buffer, filename="puzzle_piece.png")
        except Exception as e:
            logger.exception(f"Failed to process image for drop. Using raw file. Error: {e}")
            # Use the full_path here as well for the fallback.
            file = discord.File(full_path, filename="puzzle_piece.png")

        emoji = config.CUSTOM_EMOJI_STRING or config.DEFAULT_EMOJI
        embed = discord.Embed(
            title=f"{emoji} A Wild Puzzle Piece Appears!",
            description=f"A piece of the **{display_name}** puzzle has dropped!\nClick the button to collect it.",
            color=Colors.THEME_COLOR
        ).set_
