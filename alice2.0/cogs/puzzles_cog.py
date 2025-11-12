import discord
from discord.ext import commands
from discord import app_commands
import logging
from typing import List

from utils.db_utils import resolve_puzzle_key, get_puzzle_display_name
from ui.views import PuzzleGalleryView
from utils.theme import Emojis, Colors

logger = logging.getLogger(__name__)

class PuzzlesCog(commands.Cog, name="Puzzles"):
    """Commands for viewing puzzle progress and leaderboards."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def puzzle_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        """Autocomplete for puzzle names, showing the display name."""
        puzzles = self.bot.data.get("puzzles", {})
        choices = [
            app_commands.Choice(name=meta.get("display_name", slug), value=slug)
            for slug, meta in puzzles.items()
            if current.lower() in slug.lower() or current.lower() in meta.get("display_name", slug).lower()
        ]
        return choices[:25]

    @commands.hybrid_command(name="gallery", description="Browse through all the puzzles you have started.")
    async def gallery(self, ctx: commands.Context):
        """Shows an interactive gallery of all puzzles the user has pieces for."""
        await ctx.defer(ephemeral=False)
        logger.info(f"[DEBUG] /gallery invoked by {ctx.author} ({ctx.author.id})")

        user_pieces = self.bot.data.get("user_pieces", {})
        user_puzzles = user_pieces.get(str(ctx.author.id), {})

        user_puzzle_keys = [key for key, pieces in user_puzzles.items() if pieces]
        user_puzzle_keys.sort(key=lambda key: get_puzzle_display_name(self.bot.data, key))

        if not user_puzzle_keys:
            return await ctx.send("You haven't collected any puzzle pieces yet! Go find some!", ephemeral=True)

        view = PuzzleGalleryView(self.bot, ctx.interaction, user_puzzle_keys)
        embed, file = await view.generate_embed_and_file()

        await ctx.send(embed=embed, file=file, view=view, ephemeral=False)

    @commands.hybrid_command(name="leaderboard", description="Show the top collectors for a puzzle.")
    @app_commands.autocomplete(puzzle_name=puzzle_autocomplete)
    async def leaderboard(self, ctx: commands.Context, *, puzzle_name: str):
        """Displays the leaderboard for a specific puzzle."""
        await ctx.defer(ephemeral=False)
        puzzle_key = resolve_puzzle_key(self.bot.data, puzzle_name)
        if not puzzle_key:
            return await ctx.send(f"{Emojis.FAILURE} Puzzle not found: `{puzzle_name}`", ephemeral=True)

        all_user_pieces = self.bot.data.get("user_pieces", {})
        leaderboard_data = [
            (int(user_id), len(user_puzzles[puzzle_key]))
            for user_id, user_puzzles in all_user_pieces.items()
            if puzzle_key in user_puzzles and len(user_puzzles[puzzle_key]) > 0
        ]
        leaderboard_data.sort(key=lambda x: (-x[1], x[0]))

        display_name = get_puzzle_display_name(self.bot.data, puzzle_key)
        embed = discord.Embed(title=f"{Emojis.TROPHY} Leaderboard for {display_name}", color=Colors.THEME_COLOR)

        if not leaderboard_data:
            embed.description = "No one has collected any pieces for this puzzle yet."
        else:
            lines = []
            for i, (user_id, count) in enumerate(leaderboard_data[:20], start=1):
                user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
                user_mention = user.mention if user else f"User (`{user_id}`)"
                lines.append(f"**{i}.** {user_mention} - `{count}` pieces")
            embed.description = "\n".join(lines)

        await ctx.send(embed=embed, ephemeral=False)

async def setup(bot: commands.Bot):
    await bot.add_cog(PuzzlesCog(bot))