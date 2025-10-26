# cogs/puzzles_cog.py
import os
import re
import hashlib
import logging
from typing import Any, Optional

import discord
from discord.ext import commands
from discord import app_commands, File
from PIL import Image

from cogs.db_utils import slugify_key, write_preview
from render_progress import render_progress_image
from tools.preview_cache import (
    preview_cache_path,
    get_cache_dir,
    invalidate_user_puzzle_cache,
)

logger = logging.getLogger(__name__)

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

    @commands.hybrid_command(
        name="viewpuzzle",
        description="View a user's progress on a puzzle (mention a user to view theirs)"
    )
    @app_commands.autocomplete(puzzle=puzzle_autocomplete)
    async def viewpuzzle(self, ctx: commands.Context, puzzle: str, member: discord.Member = None):
        target = member or ctx.author
        user_id_str = str(target.id)

        # --- resolve puzzle_key robustly (keys, display_name, folder)
        puzzles_map: dict[str, Any] = self.bot.data.get("puzzles", {}) or {}
        puzzle_key: Optional[str] = None

        if puzzle in puzzles_map:
            puzzle_key = puzzle

        if puzzle_key is None:
            for key in puzzles_map:
                if key.lower() == puzzle.lower():
                    puzzle_key = key
                    break

        if puzzle_key is None:
            for key, info in puzzles_map.items():
                display = str(info.get("display_name", "") or "")
                if display.lower() == puzzle.lower():
                    puzzle_key = key
                    break

        if puzzle_key is None:
            puzzles_root = os.path.join(os.getcwd(), "puzzles")
            candidates = [puzzle, puzzle.replace("_", " "), puzzle.title()]
            for cand in candidates:
                cand_path = os.path.join(puzzles_root, cand)
                if os.path.isdir(cand_path):
                    for key, info in puzzles_map.items():
                        if key == cand or str(info.get("display_name", "")).lower() == cand.lower():
                            puzzle_key = key
                            break
                    if puzzle_key:
                        break

        if not puzzle_key:
            await ctx.reply(f"❌ Puzzle `{puzzle}` not found.", ephemeral=True)
            return

        display_name = puzzles_map.get(puzzle_key, {}).get("display_name", puzzle_key)

        # --- puzzle folder
        puzzle_folder = os.path.join(os.getcwd(), "puzzles", puzzle_key)
        if not os.path.isdir(puzzle_folder):
            alt = os.path.join(os.getcwd(), "puzzles", display_name)
            if os.path.isdir(alt):
                puzzle_folder = alt
            else:
                await ctx.reply(f"Puzzle folder not found for `{display_name}`.", ephemeral=True)
                return

        # --- rows/cols (prefer stored config)
        puzzle_cfg = puzzles_map.get(puzzle_key, {}) or {}
        rows = puzzle_cfg.get("rows") or puzzle_cfg.get("r") or None
        cols = puzzle_cfg.get("cols") or puzzle_cfg.get("c") or None
        total_cfg = puzzle_cfg.get("total") or puzzle_cfg.get("pieces") or None

        total_from_cfg = None
        if isinstance(total_cfg, int):
            total_from_cfg = total_cfg
        elif isinstance(total_cfg, dict):
            total_from_cfg = len(total_cfg)
        if rows is None or cols is None:
            if total_from_cfg:
                try:
                    root = int(int(total_from_cfg) ** 0.5)
                    rows = cols = root
                except Exception:
                    rows = cols = 4
            else:
                rows = cols = 4
        rows = int(rows)
        cols = int(cols)

        # --- owned pieces (from bot.collected)
        owned = list(self.bot.collected.get("user_pieces", {}).get(user_id_str, {}).get(puzzle_key, []))
        owned = [str(x) for x in owned]
        owned_count = len(owned)

        # --- total pieces (prefer bot.data then disk)
        total = None
        stored_pieces = self.bot.data.get("pieces", {}).get(puzzle_key)
        if stored_pieces:
            try:
                total = int(len(stored_pieces))
            except Exception:
                total = None

        if not total:
            pieces_dir = os.path.join(puzzle_folder, "pieces")
            if os.path.isdir(pieces_dir):
                files = [f for f in os.listdir(pieces_dir) if f.lower().endswith(".png")]
                total = len(files)
            else:
                total = 0
        total = int(total or 0)

        # --- cache lookup
        cache_path = preview_cache_path(puzzle_key, user_id_str, owned)
        if os.path.isfile(cache_path):
            out_path = cache_path
        else:
            # render into cache path
            try:
                os.makedirs(os.path.dirname(cache_path), exist_ok=True)
                out_path = render_progress_image(
                    puzzle_folder=puzzle_folder,
                    collected_piece_ids=owned,
                    rows=rows,
                    cols=cols,
                    puzzle_config=puzzle_cfg,
                    output_path=cache_path,
                )
            except Exception as e:
                await ctx.reply(f"❌ Failed to render progress image: {e}", ephemeral=True)
                return

        # --- reply
        file = File(out_path, filename="progress.png")
        embed = discord.Embed(
            title=f"🧩 Progress for {display_name}",
            description=f"Collected {owned_count} / {total}",
            color=discord.Color.purple()
        )
        embed.set_image(url="attachment://progress.png")
        if member:
            embed.set_footer(text=f"Showing progress for {member.display_name}")
        else:
            embed.set_footer(text="Showing your progress")

        await ctx.reply(embed=embed, file=file)

    # optional admin command to invalidate cache for a user/puzzle
    @commands.hybrid_command(name="invalidatepreview", description="Invalidate preview cache for a user and puzzle")
    async def invalidatepreview(self, ctx: commands.Context, puzzle_key: str, member: discord.Member = None):
        if ctx.guild is None or ctx.guild.id != GUILD_ID:
            return
        member = member or ctx.author
        user_id_str = str(member.id)
        removed = invalidate_user_puzzle_cache(puzzle_key, user_id_str)
        await ctx.reply(f"Removed {removed} cached preview(s).")

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
    await bot.add_cog(PuzzlesCog(bot))
