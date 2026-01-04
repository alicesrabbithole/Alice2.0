import discord
from discord.ext import commands
from discord import app_commands
import logging
from typing import Optional

from utils.db_utils import (
    save_data, sync_from_fs, backup_data, resolve_puzzle_key,
    get_puzzle_display_name, add_piece_to_user, remove_piece_from_user,
    wipe_puzzle_from_all)
from utils.log_utils import log

logger = logging.getLogger(__name__)

class AdminCog(commands.Cog, name="Owner"):
    """Owner-only commands for bot administration."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def puzzle_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        """Autocomplete for puzzle names."""
        puzzles = self.bot.data.get("puzzles", {})
        choices = []
        for slug, _ in puzzles.items():
            display_name = get_puzzle_display_name(self.bot.data, slug)
            if current.lower() in slug.lower() or current.lower() in display_name.lower():
                choices.append(app_commands.Choice(name=display_name, value=slug))
        return choices[:25]

    @commands.hybrid_command(name="reload", description="[Owner] Reloads all cogs and syncs commands.")
    @commands.is_owner()
    async def reload(self, ctx: commands.Context):
        """Reloads all cogs and re-syncs application commands."""
        await ctx.defer(ephemeral=False)
        reloaded_cogs, failed_cogs = [], []

        for extension in self.bot.initial_extensions:
            try:
                await self.bot.reload_extension(extension)
                reloaded_cogs.append(f"✅ `{extension}`")
            except Exception as e:
                logger.exception(f"Failed to reload {extension}.")
                failed_cogs.append(f"❌ `{extension}`")

        summary = "**Cog Reload Summary:**\n" + "\n".join(reloaded_cogs)
        if failed_cogs:
            summary += "\n\n**Failures:**\n" + "\n".join(failed_cogs)

        await ctx.send(summary, ephemeral=True)

    @commands.command(name="sync", description="[Owner] Force-syncs all commands with Discord.")
    @commands.is_owner()
    async def sync(self, ctx: commands.Context):
        """A command to forcefully re-sync all slash commands."""
        await ctx.defer(ephemeral=False)
        try:
            synced = await self.bot.tree.sync()
            await ctx.send(f"✅ Synced **{len(synced)}** commands globally.", ephemeral=True)
            logger.info(f"Commands forcefully synced by {ctx.author}. Synced {len(synced)} commands.")
        except Exception as e:
            logger.exception("Failed to sync commands.")
            await ctx.send(f"❌ Failed to sync commands: `{e}`", ephemeral=True)

    @commands.hybrid_command(name="addstaff", description="[Owner] Adds a user to the bot's staff list.")
    @commands.is_owner()
    async def addstaff(self, ctx: commands.Context, user: discord.Member):
        """Adds a user to the legacy staff list."""
        staff_list = self.bot.data.setdefault("staff", [])
        if str(user.id) not in staff_list:
            staff_list.append(str(user.id))
            save_data(self.bot.data)
            await ctx.send(f"✅ {user.mention} has been added to the staff list.", ephemeral=False)
            await log(self.bot, f"🔑 {user.mention} was added to staff by {ctx.author.mention}.")
        else:
            await ctx.send(f"⚠️ {user.mention} is already on the staff list.", ephemeral=False)

    @commands.hybrid_command(name="removestaff", description="[Owner] Removes a user from the bot's staff list.")
    @commands.is_owner()
    async def removestaff(self, ctx: commands.Context, user: discord.Member):
        """Removes a user from the legacy staff list."""
        staff_list = self.bot.data.get("staff", [])
        if str(user.id) in staff_list:
            staff_list.remove(str(user.id))
            save_data(self.bot.data)
            await ctx.send(f"✅ {user.mention} has been removed from the staff list.", ephemeral=False)
            await log(self.bot, f"🔑 {user.mention} was removed from staff by {ctx.author.mention}.")
        else:
            await ctx.send(f"⚠️ {user.mention} is not on the staff list.", ephemeral=False)

    @commands.hybrid_command(name="syncpuzzles", description="[Owner] Syncs puzzle data from the filesystem.")
    @commands.is_owner()
    async def syncpuzzles(self, ctx: commands.Context):
        """Syncs all puzzle data from the 'puzzles' directory."""
        await ctx.defer(ephemeral=False)
        backup_data()
        # The bot's current data is passed to the function, which returns the updated version.
        self.bot.data = sync_from_fs(self.bot.data)
        save_data(self.bot.data)
        await ctx.send(
            f"✅ Synced **{len(self.bot.data['puzzles'])}** puzzles and **{sum(len(p) for p in self.bot.data['pieces'].values())}** pieces from the filesystem.",
            ephemeral=False)
        await log(self.bot, f"🔄 Puzzles synced from filesystem by {ctx.author.mention}.")

    @commands.hybrid_command(name="givepiece", description="[Owner] Gives a puzzle piece to a user.")
    @app_commands.autocomplete(puzzle_name=puzzle_autocomplete)
    @commands.is_owner()
    async def givepiece(self, ctx: commands.Context, user: discord.Member, puzzle_name: str, piece_id: str):
        """Gives a specific puzzle piece to a user."""
        await ctx.defer(ephemeral=False)
        puzzle_key = resolve_puzzle_key(self.bot.data, puzzle_name)
        if not puzzle_key:
            return await ctx.send(f"❌ Puzzle not found: `{puzzle_name}`", ephemeral=False)

        if add_piece_to_user(self.bot.data, user.id, puzzle_key, piece_id):
            save_data(self.bot.data)
            display_name = get_puzzle_display_name(self.bot.data, puzzle_key)
            await ctx.send(f"✅ Gave piece `{piece_id}` of **{display_name}** to {user.mention}.", ephemeral=False)
            await log(self.bot,
                      f"🎁 Piece `{piece_id}` of **{display_name}** given to {user.mention} by {ctx.author.mention}.")
        else:
            await ctx.send(f"⚠️ {user.mention} already has that piece.", ephemeral=False)

    @commands.hybrid_command(name="takepiece", description="[Owner] Takes a puzzle piece from a user.")
    @app_commands.autocomplete(puzzle_name=puzzle_autocomplete)
    @commands.is_owner()
    async def takepiece(self, ctx: commands.Context, user: discord.Member, puzzle_name: str, piece_id: str):
        """Takes a specific puzzle piece from a user."""
        await ctx.defer(ephemeral=False)
        puzzle_key = resolve_puzzle_key(self.bot.data, puzzle_name)
        if not puzzle_key:
            return await ctx.send(f"❌ Puzzle not found: `{puzzle_name}`", ephemeral=False)

        if remove_piece_from_user(self.bot.data, user.id, puzzle_key, piece_id):
            save_data(self.bot.data)
            display_name = get_puzzle_display_name(self.bot.data, puzzle_key)
            await ctx.send(f"✅ Took piece `{piece_id}` of **{display_name}** from {user.mention}.", ephemeral=False)
            await log(self.bot,
                      f"💔 Piece `{piece_id}` of **{display_name}** taken from {user.mention} by {ctx.author.mention}.")
        else:
            await ctx.send(f"⚠️ {user.mention} does not have that piece.", ephemeral=False)

    @commands.hybrid_command(name="wipepuzzle", description="[Owner] Wipes all progress for a puzzle from all users.")
    @app_commands.autocomplete(puzzle_name=puzzle_autocomplete)
    @commands.is_owner()
    async def wipepuzzle(self, ctx: commands.Context, puzzle_name: str):
        """Wipes all collected pieces for a specific puzzle from everyone."""
        await ctx.defer(ephemeral=True)
        puzzle_key = resolve_puzzle_key(self.bot.data, puzzle_name)
        if not puzzle_key:
            return await ctx.send(f"❌ Puzzle not found: `{puzzle_name}`", ephemeral=False)

        wiped_count = wipe_puzzle_from_all(self.bot.data, puzzle_key)
        save_data(self.bot.data)
        display_name = get_puzzle_display_name(self.bot.data, puzzle_key)
        await ctx.send(
            f"✅ Wiped all progress for **{display_name}**. Removed data from **{wiped_count}** users.",
            ephemeral=False)
        await log(self.bot, f"💥 All progress for **{display_name}** was wiped by {ctx.author.mention}.")


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))