import discord
from discord.ext import commands
from discord import app_commands
import os
import asyncio
import json
from cogs.db_utils import save_data, backup_data, get_drop_channels, slugify_key
from tools.puzzle_tools import slugify, add_puzzle_from_existing

GUILD_ID = 1309962372269609010

async def puzzle_autocomplete(interaction: discord.Interaction, current):
    puzzles = interaction.client.data.get("puzzles", {}) or {}
    # use puzzles dict (slug -> meta)
    choices = []
    for slug, meta in puzzles.items():
        display = (meta or {}).get("display_name") or slug.replace("_", " ").title()
        if current.lower() in slug.lower() or current.lower() in display.lower():
            choices.append(app_commands.Choice(name=f"{display}", value=slug))
    # limit to 25 if needed
    return choices[:25]

class PuzzleAdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def is_staff(self, user: discord.Member) -> bool:
        return str(user.id) in self.bot.data.get("staff", []) or user.guild_permissions.administrator

    @commands.hybrid_command(name="importpuzzle", description="Import a built puzzle folder into config")
    @commands.is_owner()
    async def importpuzzle(self, ctx, display_name: str, puzzle_dir: str, rows: int = 4, cols: int = 4, overwrite: bool = False):
        """
        Usage examples:
        /importpuzzle "My Puzzle" puzzles/my_puzzle 4 4
        - display_name: human title
        - puzzle_dir: path to folder that contains the full image and pieces subfolder
        - rows cols: grid size (defaults to 4 4)
        - overwrite: optional flag to replace existing config entry
        """
        await ctx.defer(ephemeral=True)

        key = slugify(display_name)
        full_image = os.path.join(puzzle_dir, f"{key}_full.png")
        pieces_dir = os.path.join(puzzle_dir, "pieces")
        config_path = "config.json"

        changed, msgs = add_puzzle_from_existing(
            config_path=config_path,
            puzzle_key=key,
            display_name=display_name,
            full_image_path=full_image,
            pieces_dir=pieces_dir,
            rows=rows,
            cols=cols,
            overwrite=overwrite
        )

        reply = "\n".join(msgs)
        if changed:
            reply = f"✅ Imported puzzle {display_name} as `{key}`\n" + reply

        await ctx.reply(reply, ephemeral=True)


    @commands.hybrid_command(
        name="deletepuzzle",
        description="Delete a puzzle and all its data (owner only)",
        extras={"category": "Puzzles", "owner": True}
    )
    @commands.is_owner()
    @app_commands.describe(puzzle="Puzzle key to delete")
    async def deletepuzzle(self, ctx: commands.Context, puzzle: str):
        puzzle_key = puzzle.lower()
        if puzzle_key not in self.bot.data.get("puzzles", {}):
            await ctx.reply(f"❌ Puzzle `{puzzle}` not found.", ephemeral=True)
            return

        await ctx.reply(f"⚠️ Are you sure you want to delete `{puzzle_key}`?\nType the puzzle key to confirm.", ephemeral=True)

        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel

        try:
            reply = await self.bot.wait_for("message", timeout=30.0, check=check)
        except asyncio.TimeoutError:
            await ctx.send("⏳ Deletion cancelled (timeout).", ephemeral=True)
            return

        if reply.content.strip().lower() != puzzle_key:
            await ctx.send("❌ Confirmation failed. Puzzle not deleted.", ephemeral=True)
            return

        backup_data()

        # Delete folders
        try:
            os.remove(self.bot.data["puzzles"][puzzle_key]["full_image"])
            piece_folder = os.path.join("puzzles", puzzle_key, "pieces")
            if os.path.exists(piece_folder):
                for f in os.listdir(piece_folder):
                    os.remove(os.path.join(piece_folder, f))
                os.rmdir(piece_folder)
            os.rmdir(os.path.join("puzzles", puzzle_key))
        except Exception as e:
            await ctx.send(f"⚠️ Error deleting files: {e}", ephemeral=True)
            return

        # Remove from config
        self.bot.data["puzzles"].pop(puzzle_key, None)
        self.bot.data.get("pieces", {}).pop(puzzle_key, None)

        with open("config.json", "w", encoding="utf-8") as f:
            json.dump(self.bot.data, f, indent=2)

        # Remove user progress
        for uid in self.bot.collected.get("user_pieces", {}):
            self.bot.collected["user_pieces"][uid].pop(puzzle_key, None)

        save_data(self.bot.collected)
        await ctx.send(f"🗑️ Puzzle `{puzzle_key}` deleted and backup saved.", ephemeral=True)

    @commands.hybrid_command(
        name="removeuserpieces",
        description="Clear puzzle progress for a user or everyone",
        extras={"category": "Puzzles"}
    )
    @app_commands.describe(puzzle="Puzzle key", user="User to clear (leave blank to clear all)")
    async def removeuserpieces(self, ctx: commands.Context, puzzle: str, user: discord.User = None):
        if not self.is_staff(ctx.author):
            await ctx.reply("❌ You don’t have permission to run this command.", ephemeral=True)
            return

        puzzle_key = puzzle.lower()
        if puzzle_key not in self.bot.data.get("pieces", {}):
            await ctx.reply(f"❌ Puzzle `{puzzle}` not found.", ephemeral=True)
            return

        backup_data()

        if user:
            uid = str(user.id)
            if uid in self.bot.collected.get("user_pieces", {}) and puzzle_key in self.bot.collected["user_pieces"][uid]:
                self.bot.collected["user_pieces"][uid].pop(puzzle_key, None)
                save_data(self.bot.collected)
                await ctx.reply(f"🧹 Cleared `{puzzle}` progress for {user.display_name}.", ephemeral=True)
            else:
                await ctx.reply(f"{user.display_name} has no progress on `{puzzle}`.", ephemeral=True)
        else:
            for uid in self.bot.collected.get("user_pieces", {}):
                self.bot.collected["user_pieces"][uid].pop(puzzle_key, None)
            save_data(self.bot.collected)
            await ctx.reply(f"🧹 Cleared `{puzzle}` progress for all users.", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(PuzzleAdminCog(bot))