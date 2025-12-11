import discord
from discord.ext import commands
from discord import app_commands
import logging
from typing import List, Optional

from utils.db_utils import resolve_puzzle_key, get_puzzle_display_name, save_data
from ui.views import PuzzleGalleryView, open_leaderboard_view, LeaderboardView
from utils.theme import Emojis, Colors
from utils.checks import is_admin

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
        """Shows an interactive gallery of all puzzles — include puzzles with no collected pieces as well."""
        await ctx.defer(ephemeral=False)
        logger.info(f"[DEBUG] /gallery invoked by {ctx.author} ({ctx.author.id})")

        # Build the list of all puzzles (sorted by display name)
        all_puzzles = list(self.bot.data.get("puzzles", {}).keys())
        all_puzzles.sort(key=lambda key: get_puzzle_display_name(self.bot.data, key))

        # Filter out puzzles hidden from members unless the invoker is an admin (manage_guild or guild owner)
        hidden = set(self.bot.data.get("hidden_puzzles", []))
        try:
            is_guild_context = ctx.guild is not None
            is_admin_user = False
            if is_guild_context:
                # treat guild owner and users with manage_guild as admins for visibility purposes
                is_admin_user = ctx.author.id == getattr(ctx.guild, "owner_id", None) or ctx.author.guild_permissions.manage_guild
            # If not admin, filter hidden puzzles out
            if not is_admin_user and hidden:
                all_puzzles = [k for k in all_puzzles if k not in hidden]
        except Exception:
            # If anything goes wrong, default to not filtering
            logger.exception("Error while checking admin permissions for gallery filtering")

        all_puzzles.sort(key=lambda key: get_puzzle_display_name(self.bot.data, key))

        # The user's collected puzzle keys (may be absent or empty)
        user_pieces = self.bot.data.get("user_pieces", {})
        user_puzzles = user_pieces.get(str(ctx.author.id), {})

        # Build gallery list: put puzzles the user has any pieces for first, then the rest.
        puzzles_with_pieces = [k for k in all_puzzles if k in user_puzzles and user_puzzles.get(k)]
        puzzles_without_pieces = [k for k in all_puzzles if k not in puzzles_with_pieces]
        user_puzzle_keys = puzzles_with_pieces + puzzles_without_pieces

        # If there are no puzzles configured at all, inform the user.
        if not user_puzzle_keys:
            return await ctx.send("There are no puzzles configured yet.", ephemeral=True)

        # Pass the interaction when available so the view can edit the original response later.
        interaction = getattr(ctx, "interaction", None)
        view = PuzzleGalleryView(self.bot, interaction, user_puzzle_keys, current_index=0, owner_id=ctx.author.id)
        embed, file = await view.generate_embed_and_file()

        # Send using interaction context if available (slash), otherwise ctx.send works for prefix.
        if interaction:
            # For slash commands we can attach view and file directly via ctx.send
            await ctx.send(embed=embed, file=file, view=view, ephemeral=False)
        else:
            await ctx.send(embed=embed, file=file, view=view)

    @commands.hybrid_command(name="leaderboard", description="Show the top collectors for a puzzle.")
    @app_commands.autocomplete(puzzle_name=puzzle_autocomplete)
    async def leaderboard(self, ctx: commands.Context, *, puzzle_name: str):
        """Displays the leaderboard for a specific puzzle using the shared LeaderboardView (gallery-styled)."""
        await ctx.defer(ephemeral=False)
        puzzle_key = resolve_puzzle_key(self.bot.data, puzzle_name)
        if not puzzle_key:
            return await ctx.send(f"{Emojis.FAILURE} Puzzle not found: `{puzzle_name}`", ephemeral=True)

        # If invoked as a slash command, prefer the interaction-based helper (keeps behavior consistent).
        interaction = getattr(ctx, "interaction", None)
        if interaction:
            # open_leaderboard_view will handle deferring/followup appropriately
            return await open_leaderboard_view(self.bot, interaction, puzzle_key)

        # Fallback for prefix invocation: build leaderboard data and construct the LeaderboardView directly.
        all_user_pieces = self.bot.data.get("user_pieces", {})
        leaderboard_data = [
            (int(user_id), len(user_puzzles.get(puzzle_key, [])))
            for user_id, user_puzzles in all_user_pieces.items()
            if puzzle_key in user_puzzles and len(user_puzzles[puzzle_key]) > 0
        ]
        leaderboard_data.sort(key=lambda x: (-x[1], x[0]))

        # Use the same LeaderboardView from ui.views so the styling/pagination matches the gallery.
        view = LeaderboardView(self.bot, ctx.guild, puzzle_key, leaderboard_data, page=0)
        embed = await view.generate_embed()
        await ctx.send(embed=embed, view=view, ephemeral=False)

    @commands.hybrid_command(name="firstfinisher", description="Show who finished a puzzle first!")
    @app_commands.autocomplete(puzzle_name=puzzle_autocomplete)
    async def firstfinisher(self, ctx: commands.Context, *, puzzle_name: str):
        puzzle_key = resolve_puzzle_key(self.bot.data, puzzle_name)
        finishers = self.bot.data.get("puzzle_finishers", {}).get(puzzle_key, [])
        if not finishers:
            return await ctx.send("No one has completed this puzzle yet!", ephemeral=True)
        first = finishers[0]
        user = self.bot.get_user(first["user_id"]) or await self.bot.fetch_user(first["user_id"])
        await ctx.send(
            f"The first person to complete **{get_puzzle_display_name(self.bot.data, puzzle_key)}** was: {user.mention}`!",
            ephemeral=False
        )

    # -------------------------
    # Admin commands to hide/unhide puzzles from member galleries
    # -------------------------
    @commands.hybrid_command(name="puzzle_hide", description="Hide a puzzle so it does not appear in member galleries.")
    @app_commands.autocomplete(puzzle_name=puzzle_autocomplete)
    @is_admin()
    async def puzzle_hide(self, ctx: commands.Context, *, puzzle_name: str):
        """Admin: hide a puzzle (prevents it from showing in non-admin users' /gallery)."""
        puzzle_key = resolve_puzzle_key(self.bot.data, puzzle_name)
        if not puzzle_key:
            return await ctx.send(f"❌ Puzzle not found: `{puzzle_name}`", ephemeral=True)

        hidden = set(self.bot.data.setdefault("hidden_puzzles", []))
        if puzzle_key in hidden:
            return await ctx.send(f"ℹ️ Puzzle **{get_puzzle_display_name(self.bot.data, puzzle_key)}** is already hidden.", ephemeral=True)

        hidden.add(puzzle_key)
        # persist as list for JSON
        self.bot.data["hidden_puzzles"] = list(hidden)
        save_data(self.bot.data)
        await ctx.send(f"✅ Hidden puzzle **{get_puzzle_display_name(self.bot.data, puzzle_key)}** from member galleries.", ephemeral=True)

    @commands.hybrid_command(name="puzzle_unhide", description="Unhide a previously hidden puzzle so it appears in galleries again.")
    @app_commands.autocomplete(puzzle_name=puzzle_autocomplete)
    @is_admin()
    async def puzzle_unhide(self, ctx: commands.Context, *, puzzle_name: str):
        """Admin: unhide a puzzle (restores visibility in /gallery)."""
        puzzle_key = resolve_puzzle_key(self.bot.data, puzzle_name)
        if not puzzle_key:
            return await ctx.send(f"❌ Puzzle not found: `{puzzle_name}`", ephemeral=True)

        hidden = set(self.bot.data.get("hidden_puzzles", []))
        if puzzle_key not in hidden:
            return await ctx.send(f"ℹ️ Puzzle **{get_puzzle_display_name(self.bot.data, puzzle_key)}** is not hidden.", ephemeral=True)

        hidden.remove(puzzle_key)
        self.bot.data["hidden_puzzles"] = list(hidden)
        save_data(self.bot.data)
        await ctx.send(f"✅ Unhidden puzzle **{get_puzzle_display_name(self.bot.data, puzzle_key)}**; members will now see it in galleries.", ephemeral=True)

    @commands.hybrid_command(name="puzzle_hidden_list", description="List puzzles currently hidden from member galleries.")
    @is_admin()
    async def puzzle_hidden_list(self, ctx: commands.Context):
        """Admin: list which puzzles are hidden."""
        hidden = list(self.bot.data.get("hidden_puzzles", []))
        if not hidden:
            return await ctx.send("No puzzles are currently hidden.", ephemeral=True)
        lines = []
        for key in hidden:
            display = get_puzzle_display_name(self.bot.data, key)
            lines.append(f"- {display} (`{key}`)")
        await ctx.send("Hidden puzzles:\n" + "\n".join(lines), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(PuzzlesCog(bot))