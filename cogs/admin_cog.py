import discord
from discord.ext import commands
from discord import app_commands, Interaction
import logging
from typing import Optional

from .utils.db_utils import save_data, is_staff, sync_from_fs, backup_data, resolve_puzzle_key, get_puzzle_display_name, \
    add_piece_to_user, remove_piece_from_user, wipe_puzzle_from_all
from .utils.log_utils import log

logger = logging.getLogger(__name__)


async def puzzle_autocomplete(interaction: Interaction, current: str) -> list[app_commands.Choice[str]]:
    puzzles = getattr(interaction.client, "data", {}).get("puzzles", {}) or {}
    choices = []
    for slug, _ in puzzles.items():
        display_name = get_puzzle_display_name({}, slug)
        if current.lower() in slug.lower() or current.lower() in display_name.lower():
            choices.append(app_commands.Choice(name=display_name, value=slug))
    return choices[:25]


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ... (all other commands are unchanged) ...

    @commands.hybrid_command(name="reload", description="[Owner] Reloads all cogs and syncs commands.")
    @commands.is_owner()
    async def reload(self, ctx: commands.Context):
        await ctx.defer(ephemeral=True)
        reloaded_cogs, failed_cogs = [], []
        for extension in self.bot.initial_extensions:
            try:
                await self.bot.reload_extension(extension)
                reloaded_cogs.append(f"✅ `{extension}`")
            except Exception as e:
                logger.exception(f"Failed to reload {extension}.")
                failed_cogs.append(f"❌ `{extension}`")
        try:
            await self.bot.tree.sync()
            reloaded_cogs.append("✅ `Commands Synced`")
        except Exception as e:
            logger.exception("Failed to sync commands.")
            failed_cogs.append(f"❌ `Command Sync Failed`")
        summary = "\n".join(reloaded_cogs)
        if failed_cogs:
            summary += "\n\n**Failures:**\n" + "\n".join(failed_cogs)
        await ctx.send(f"**Cog Reload Summary:**\n{summary}", ephemeral=True)

    # --- THIS IS THE NEW COMMAND ---
    @commands.command(name="sync", description="[Owner] Force-syncs all commands with Discord.")
    @commands.is_owner()
    async def sync(self, ctx: commands.Context):
        """A command to forcefully clear and re-sync all slash commands."""
        await ctx.send("⏳ Forcefully syncing commands...", ephemeral=True)
        # Clear existing commands
        self.bot.tree.clear_commands(guild=None)
        await self.bot.tree.sync()

        # Re-sync from the bot's tree
        synced = await self.bot.tree.sync()
        await ctx.send(f"✅ Synced **{len(synced)}** commands globally.", ephemeral=True)
        logger.info(f"Commands forcefully synced by {ctx.author}. Synced {len(synced)} commands.")


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))