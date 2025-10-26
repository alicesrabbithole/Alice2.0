import discord
from discord.ext import commands
from discord import app_commands, Interaction
from cogs.db_utils import sync_puzzle_images, slugify_key, get_drop_channels
from PIL import Image
import re
import os
import pathlib

GUILD_ID = 1309962372269609010

# --- module-level autocomplete ---
async def puzzle_key_autocomplete(interaction: Interaction, current: str):
    return [
        app_commands.Choice(name=p, value=p)
        for p in interaction.client.data.get("puzzles", {})
        if current.lower() in p.lower()
    ][:25]


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # sync_puzzle_images may have different signatures or return None.
        # Be defensive: call it if available and tolerate None or different return shapes.
        self.puzzles, self.pieces, self.names = {}, {}, {}
        try:
            res = sync_puzzle_images(self.bot)
            if isinstance(res, dict):
                # some syncs return a dict with subkeys
                self.puzzles = res.get("puzzles", {}) or {}
                self.pieces = res.get("pieces", {}) or {}
                # optional name map
                self.names = res.get("names", {}) or {}
            elif isinstance(res, tuple) and len(res) == 3:
                self.puzzles, self.pieces, self.names = res
            elif isinstance(res, tuple) and len(res) == 2:
                self.puzzles, self.pieces = res
            # otherwise leave defaults (bot.data will be used at runtime)
        except Exception:
            # swallow here so cog still loads; runtime will read bot.data as source of truth
            pass

    async def puzzle_key_autocomplete(self, interaction: Interaction, current: str):
        return [
            app_commands.Choice(name=p, value=p)
            for p in self.bot.data.get("puzzles", {})
            if current.lower() in p.lower()
        ][:25]

    async def cog_check(self, ctx: commands.Context) -> bool:
        """Restrict all commands in this cog to the configured guild and staff/admins."""
        if ctx.guild is None or ctx.guild.id != GUILD_ID:
            return False
        staff_ids = self.bot.data.get("staff", [])
        return str(ctx.author.id) in staff_ids or ctx.author.guild_permissions.administrator

    @commands.hybrid_command(
        name="alicehelp",
        description="Show all available Alice commands (optionally filter by category)"
    )
    @app_commands.describe(category="Optional category filter (Puzzle, Drop, Staff, Other)")
    async def alicehelp(self, ctx: commands.Context, category: str = None):
        embed = discord.Embed(
            title="🧩 Alice Bot Commands",
            description="Here’s what you can do with Alice:",
            color=discord.Color.purple()
        )

        categories: dict[str, list[str]] = {}
        for command in self.bot.commands:
            if command.hidden:
                continue
            cat = command.extras.get("category", "Other")
            owner_only = command.extras.get("owner", False)

            desc = command.description or "No description"
            if owner_only:
                desc += " 🔒 (Owner Only)"

            entry = f"`/{command.name}` — {desc}"
            categories.setdefault(cat, []).append(entry)

        category_order = ["Puzzle", "Drop", "Staff", "Other"]

        if category:
            category = category.capitalize()
            if category not in categories:
                await ctx.reply(f"⚠️ No commands found for category `{category}`.", ephemeral=True)
                return
            embed.add_field(
                name=f"{category} Commands",
                value="\n".join(sorted(categories[category])),
                inline=False
            )
        else:
            for cat in category_order:
                if cat in categories:
                    embed.add_field(
                        name=f"{cat} Commands",
                        value="\n".join(sorted(categories[cat])),
                        inline=False
                    )

        embed.set_footer(text="Use / before each command. 🔒 = Owner Only")
        await ctx.reply(embed=embed, ephemeral=False)

    @commands.command(name="previewpuzzle")
    @commands.is_owner()
    async def preview_puzzle(self, ctx, puzzle_key: str, rows: int, cols: int):
        pieces_folder = pathlib.Path("pieces") / puzzle_key
        output_path = pathlib.Path("puzzles") / puzzle_key / "preview.png"

        if not pieces_folder.exists():
            await ctx.send(f"❌ Folder not found: `{pieces_folder}`")
            return

        collected_ids = [str(i) for i in range(1, rows * cols + 1)]

        try:
            preview = self.generate_ghost_preview(pieces_folder, collected_ids, rows, cols)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            preview.save(output_path)
            await ctx.send(file=discord.File(output_path))
        except Exception as e:
            await ctx.send(f"❌ Failed to generate preview: {e}")

    @staticmethod
    def generate_ghost_preview(folder: pathlib.Path, collected_ids, rows, cols):
        files = [f for f in os.listdir(folder) if re.match(r"p1_\d+\.png", f)]
        files.sort(key=lambda x: int(re.findall(r"\d+", x)[0]))

        first = Image.open(folder / files[0])
        tile_w, tile_h = first.size
        preview = Image.new("RGBA", (tile_w * cols, tile_h * rows), (0, 0, 0, 0))

        count = 0
        for row in range(rows):
            for col in range(cols):
                piece_id = str(count + 1)
                piece = Image.open(folder / files[count])

                if piece_id in collected_ids:
                    preview.paste(piece, (col * tile_w, row * tile_h))
                else:
                    ghost = piece.copy()
                    ghost.putalpha(80)
                    preview.paste(ghost, (col * tile_w, row * tile_h))

                count += 1

        return preview

    @commands.command(name="validateconfig")
    @commands.is_owner()
    async def validate_config(self, ctx):
        report = []
        puzzles = self.bot.data.get("puzzles", {})
        pieces = self.bot.data.get("pieces", {})
        # use bot.data (collected is non-standard in your file)
        user_pieces = self.bot.data.get("user_pieces", {})

        for key in puzzles:
            if key not in pieces:
                report.append(f"🧩 Puzzle `{key}` is missing a `pieces` block in config.json.")
                continue

            if "grid" not in puzzles[key]:
                report.append(f"⚠️ Puzzle `{key}` is missing a `grid` in config.json.")

            expected = puzzles[key].get("grid", [4, 4])
            total = expected[0] * expected[1]
            piece_map = pieces.get(key, {})
            actual = len(piece_map)

            if actual != total:
                report.append(f"🧩 Puzzle `{key}` has {actual}/{total} pieces.")

            for pid in list(piece_map.keys()):
                try:
                    idx = int(pid)
                except ValueError:
                    report.append(f"⚠️ Puzzle `{key}` has non-integer piece ID: `{pid}`")
                    continue
                if idx < 1 or idx > total:
                    report.append(f"⚠️ Piece `{pid}` in `{key}` is out of bounds.")

                path = piece_map.get(pid)
                if path and not os.path.exists(path):
                    report.append(f"❌ Missing file: `{path}` (piece {pid} of `{key}`)")

        used_keys = set()
        for uid, puzzles_dict in user_pieces.items():
            used_keys.update(puzzles_dict.keys())
        unused = set(pieces.keys()) - used_keys
        for key in unused:
            report.append(f"🕳️ Puzzle `{key}` is unused by any user.")

        await ctx.send("\n".join(report[:50]) or "✅ No issues found.")

async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))
