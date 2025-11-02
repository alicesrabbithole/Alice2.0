import discord
from discord.ext import commands
from discord import app_commands, File
import io
import logging
from typing import List

import config
from .utils.db_utils import resolve_puzzle_key, get_puzzle_display_name
from .ui.overlay import render_progress_image
from .ui.views import PuzzleGalleryView
from .ui.theme import Emojis, Colors

logger = logging.getLogger(__name__)


class PuzzlesCog(commands.Cog, name="Puzzles"):
    """Commands for viewing puzzle progress and leaderboards."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def puzzle_autocomplete(self, interaction: discord.Interaction, current: str) -> List[
        app_commands.Choice[str]]:
        """Autocomplete for puzzle names, showing the display name."""
        puzzles = self.bot.data.get("puzzles", {})
        choices = []
        for slug, meta in puzzles.items():
            # --- THIS IS THE FIX FOR AUTOCOMPLETE ---
            # Use the display_name for the user-facing name, and the slug for the internal value.
            display_name = meta.get("display_name", slug)
            if current.lower() in slug.lower() or current.lower() in display_name.lower():
                choices.append(app_commands.Choice(name=display_name, value=slug))
            # --- End of fix ---
        return choices[:25]

    @commands.hybrid_command(name="viewpuzzle", description="View your progress on a specific puzzle.")
    @app_commands.autocomplete(puzzle_name=puzzle_autocomplete)
    async def viewpuzzle(self, ctx: commands.Context, *, puzzle_name: str):
        """Shows your current progress on a selected puzzle."""
        await ctx.defer()

        # The puzzle_name passed here will be the SLUG (e.g., "alice_test") because we set it as the `value` in the Choice.
        puzzle_key = resolve_puzzle_key(self.bot.data, puzzle_name)
        if not puzzle_key:
            return await ctx.send(f"{Emojis.FAILURE} Puzzle not found: `{puzzle_name}`", ephemeral=True)

        # --- THIS IS THE FIX FOR THE EMBED ---
        # We fetch the display_name using our utility function.
        display_name = get_puzzle_display_name(self.bot.data, puzzle_key)
        # --- End of fix ---

        user_pieces = self.bot.data.get("user_pieces", {}).get(str(ctx.author.id), {}).get(puzzle_key, [])
        total_pieces = len(self.bot.data.get("pieces", {}).get(puzzle_key, {}))

        embed = discord.Embed(
            title=f"{Emojis.PUZZLE_PIECE} {display_name}",  # Use the correct display_name
            description=f"**Progress:** {len(user_pieces)} / {total_pieces} pieces collected.",
            color=Colors.PRIMARY
        ).set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)

        try:
            image_bytes = render_progress_image(self.bot.data, puzzle_key, user_pieces)
            filename = f"{puzzle_key}_progress.png"
            file = File(io.BytesIO(image_bytes), filename=filename)
            embed.set_image(url=f"attachment://{filename}")
            await ctx.send(embed=embed, file=file)
        except Exception as e:
            logger.exception(f"Error rendering puzzle view for {puzzle_key}: {e}")
            await ctx.send(f"{Emojis.WARNING} An unexpected error occurred while rendering the puzzle.", ephemeral=True)

    @commands.hybrid_command(name="gallery", description="Browse through all the puzzles you have started.")
    async def gallery(self, ctx: commands.Context):
        """Shows an interactive gallery of all puzzles the user has pieces for."""
        await ctx.defer(ephemeral=True)
        user_puzzles = self.bot.data.get("user_pieces", {}).get(str(ctx.author.id), {})

        user_puzzle_keys = [key for key, pieces in user_puzzles.items() if pieces]

        if not user_puzzle_keys:
            return await ctx.send("You haven't collected any puzzle pieces yet! Go find some!", ephemeral=True)

        user_puzzle_keys.sort(key=lambda key: get_puzzle_display_name(self.bot.data, key))

        view = PuzzleGalleryView(self.bot, ctx.interaction, user_puzzle_keys)
        embed, file = await view.generate_embed_and_file()  # generate_embed_and_file now returns 2 items

        await ctx.send(embed=embed, file=file, view=view, ephemeral=True)

    @commands.hybrid_command(name="leaderboard", description="Show the top collectors for a puzzle.")
    @app_commands.autocomplete(puzzle_name=puzzle_autocomplete)
    async def leaderboard(self, ctx: commands.Context, *, puzzle_name: str):
        """Displays the leaderboard for a specific puzzle."""
        await ctx.defer(ephemeral=True)
        puzzle_key = resolve_puzzle_key(self.bot.data, puzzle_name)
        if not puzzle_key:
            return await ctx.send(f"{Emojis.FAILURE} Puzzle not found: `{puzzle_name}`", ephemeral=True)

        all_user_pieces = self.bot.data.get("user_pieces", {})
        leaderboard_data = []
        for user_id, user_puzzles in all_user_pieces.items():
            if puzzle_key in user_puzzles:
                piece_count = len(user_puzzles[puzzle_key])
                if piece_count > 0:
                    leaderboard_data.append((int(user_id), piece_count))

        leaderboard_data.sort(key=lambda x: (-x[1], x[0]))

        display_name = get_puzzle_display_name(self.bot.data, puzzle_key)
        embed = discord.Embed(title=f"{Emojis.LEADERBOARD} Leaderboard for {display_name}", color=Colors.GOLD)

        if not leaderboard_data:
            embed.description = "No one has collected any pieces for this puzzle yet."
        else:
            lines = []
            for i, (user_id, count) in enumerate(leaderboard_data[:20], start=1):
                user = await self.bot.fetch_user(user_id) if self.bot.get_user(user_id) is None else self.bot.get_user(
                    user_id)
                user_mention = user.mention if user else f"User (`{user_id}`)"
                lines.append(f"**{i}.** {user_mention} - `{count}` pieces")
            embed.description = "\n".join(lines)

        await ctx.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(PuzzlesCog(bot))