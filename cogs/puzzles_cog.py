# cogs/puzzles_cog.py
import os
import re
import hashlib
import logging
from typing import Any, Optional
from ui.overlay import render_progress_image
import discord
from discord.ext import commands
from discord import app_commands, File
from PIL import Image
from cogs.db_utils import slugify_key, write_preview, resolve_puzzle_key

logger = logging.getLogger(__name__)

logger.warning("🧪 [COG NAME] loaded")

GUILD_ID = 1309962372269609010


class PuzzlesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot: commands.Bot = bot  # type: ignore

    async def puzzle_autocomplete(self, interaction: discord.Interaction, current: str):
        puzzles = self.bot.data.get("puzzles", {}) or {}
        # use puzzles dict (slug -> meta)
        choices = []
        for slug, meta in puzzles.items():
            display = (meta or {}).get("display_name") or slug.replace("_", " ").title()
            if current.lower() in slug.lower() or current.lower() in display.lower():
                choices.append(app_commands.Choice(name=f"{display}", value=slug))
        # limit to 25 if needed
        return choices[:25]

    logger.info("🔥 viewpuzzle command triggered")

    @app_commands.command(name="viewpuzzle", description="View your progress on a puzzle")
    @app_commands.describe(puzzle_name="Select a puzzle to view")
    async def viewpuzzle(self, interaction: discord.Interaction, puzzle_name: str):
        puzzle_key = resolve_puzzle_key(self.bot, puzzle_name)
        logger.debug("Resolved puzzle_key: %s", puzzle_key)
        if not puzzle_key or puzzle_key not in self.bot.data["puzzles"]:
            await interaction.response.send_message(f"⚠️ Puzzle '{puzzle_name}' not found.", ephemeral=False)
            return

        # ✅ Defer response
        await interaction.response.defer(ephemeral=False)

        puzzle = self.bot.data["puzzles"][puzzle_key]
        uid = str(interaction.user.id)
        user_pieces = self.bot.data.get("user_pieces", {}).get(uid, {}).get(puzzle_key, [])
        collected_count = len(user_pieces)
        rows = puzzle.get("rows", 4)
        cols = puzzle.get("cols", 4)
        total_pieces = rows * cols
        puzzle_cfg = puzzle.get("config", {})
        piece_map = self.bot.data.get("pieces", {}).get(puzzle_key, {})
        puzzle_folder = os.path.join(os.getcwd(), "puzzles", puzzle_key)

        flags = self.bot.data.get("user_render_flags", {}).get(uid, {}).get(puzzle_key, {})
        show_glow = flags.get("glow", False)
        show_bar = flags.get("progress_bar", False)

        logger.debug("Render flags for %s → glow=%s, bar=%s", puzzle_key, show_glow, show_bar)

        if not flags:
            logger.debug("No user-specific flags found for %s (uid=%s)", puzzle_key, uid)

        embed = discord.Embed(
            title=f"🧩 {puzzle['display_name']}",
            description=f"You’ve collected `{collected_count}/{total_pieces}` pieces.",
            color=discord.Color.purple()
        )

        if user_pieces:
            embed.add_field(
                name="Collected pieces",
                value=", ".join(sorted(user_pieces)),
                inline=False
            )
            logger.debug("User %s has pieces: %s", uid, user_pieces)
            logger.debug("User %s has pieces: %s", uid, user_pieces)
        try:
            preview_path = os.path.join("temp", f"{puzzle_key}_{uid}_progress.png")
            render_progress_image(
                puzzle_folder=puzzle_folder,
                collected_piece_ids=user_pieces,
                rows=rows,
                cols=cols,
                puzzle_config=puzzle_cfg,
                output_path=preview_path,
                piece_map=piece_map,
                show_glow=show_glow,
                show_bar=show_bar
            )

            with open(preview_path, "rb") as fh:
                file = discord.File(fh, filename="progress.png")
                embed.set_image(url="attachment://progress.png")
                await interaction.followup.send(embed=embed, file=file, ephemeral=False)

        except Exception as e:
            logger.exception("⚠️ Failed to generate or send preview: %s", e)
            await interaction.followup.send(embed=embed, content="⚠️ Preview not available yet.", ephemeral=False)

    @commands.hybrid_command(name="listpuzzles", description="List available puzzles")
    async def listpuzzles(self, ctx: commands.Context):
        if ctx.guild is None or ctx.guild.id != GUILD_ID:
            return
        puzzles = list(self.bot.data.get("puzzles", {}).keys())
        if not puzzles:
            await ctx.reply("No puzzles found.")
            return
        formatted = "\n".join(f"`{p}`" for p in puzzles)
        await ctx.reply(f"Available puzzles:\n{formatted}")


async def setup(bot: commands.Bot):
    cog = PuzzlesCog(bot)
    cog.viewpuzzle.autocomplete("puzzle_name")(cog.puzzle_autocomplete)
    await bot.add_cog(cog)


