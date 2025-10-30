import discord
from discord.ext import commands
from discord import app_commands, Interaction, File
import io
import logging

from .utils.db_utils import resolve_puzzle_key, get_puzzle_display_name
from .ui.overlay import render_progress_image
from .ui.views import CUSTOM_EMOJI_STRING, DEFAULT_EMOJI

logger = logging.getLogger(__name__)


async def puzzle_autocomplete(interaction: Interaction, current: str) -> list[app_commands.Choice[str]]:
    puzzles = getattr(interaction.client, "data", {}).get("puzzles", {}) or {}
    choices = []
    for slug, _ in puzzles.items():
        display_name = get_puzzle_display_name({}, slug)
        if current.lower() in slug.lower() or current.lower() in display_name.lower():
            choices.append(app_commands.Choice(name=display_name, value=slug))
    return choices[:25]


class PuzzlesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="viewpuzzle", description="View your progress on a specific puzzle.")
    @app_commands.autocomplete(puzzle_name=puzzle_autocomplete)
    async def viewpuzzle(self, ctx: commands.Context, *, puzzle_name: str):
        await ctx.defer()
        puzzle_key = resolve_puzzle_key(self.bot.data, puzzle_name)
        if not puzzle_key:
            await ctx.send(f"❌ Puzzle not found: `{puzzle_name}`", ephemeral=True);
            return

        display_name = get_puzzle_display_name(self.bot.data, puzzle_key)
        user_pieces = self.bot.data.get("user_pieces", {}).get(str(ctx.author.id), {}).get(puzzle_key, [])
        total_pieces = len(self.bot.data.get("pieces", {}).get(puzzle_key, {}))

        emoji = CUSTOM_EMOJI_STRING or DEFAULT_EMOJI
        embed = discord.Embed(
            title=f"{emoji} {display_name}",
            description=f"**Progress:** {len(user_pieces)} / {total_pieces} pieces collected.",
            color=discord.Color.purple()
        ).set_author(name=ctx.author.display_name, icon_url=ctx.author.display_avatar.url)

        try:
            image_bytes = render_progress_image(self.bot.data, puzzle_key, user_pieces)
            file = File(io.BytesIO(image_bytes), filename="puzzle_progress.png")
            embed.set_image(url="attachment://puzzle_progress.png")
            await ctx.send(embed=embed, file=file)
        except Exception as e:
            logger.exception(f"Error rendering puzzle view for {puzzle_key}: {e}")
            await ctx.send("⚠️ An unexpected error occurred while rendering the puzzle.", ephemeral=True)

    @commands.hybrid_command(name="leaderboard", description="Show the top collectors for a puzzle.")
    @app_commands.autocomplete(puzzle_name=puzzle_autocomplete)
    async def leaderboard(self, ctx: commands.Context, *, puzzle_name: str):
        await ctx.defer(ephemeral=True)
        puzzle_key = resolve_puzzle_key(self.bot.data, puzzle_name)
        if not puzzle_key:
            await ctx.send(f"❌ Puzzle not found: `{puzzle_name}`", ephemeral=True);
            return

        all_user_pieces = self.bot.data.get("user_pieces", {})
        leaderboard_data = []
        for user_id, user_puzzles in all_user_pieces.items():
            if puzzle_key in user_puzzles:
                piece_count = len(user_puzzles[puzzle_key])
                if piece_count > 0:
                    leaderboard_data.append((int(user_id), piece_count))

        leaderboard_data.sort(key=lambda x: (-x[1], x[0]))

        display_name = get_puzzle_display_name(self.bot.data, puzzle_key)
        emoji = CUSTOM_EMOJI_STRING or DEFAULT_EMOJI
        embed = discord.Embed(title=f"{emoji} Leaderboard for {display_name}", color=discord.Color.gold())

        if not leaderboard_data:
            embed.description = "No one has collected any pieces for this puzzle yet."
        else:
            lines = []
            for i, (user_id, count) in enumerate(leaderboard_data[:20], start=1):
                user = self.bot.get_user(user_id) or f"User ID: {user_id}"
                lines.append(f"**{i}.** {user.mention if isinstance(user, discord.User) else user} - `{count}` pieces")
            embed.description = "\n".join(lines)

        await ctx.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(PuzzlesCog(bot))