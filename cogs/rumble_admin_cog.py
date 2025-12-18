#!/usr/bin/env python3
"""
Refactored RumbleAdminCog to include:
- All original commands (restored): `rumble_remove_channel`, `rumble_test_award`, `rumble_give_part`, `rumble_take_part`.
- Persistent channel mappings via `rumble_set_channel_part`.
- Added `rumble_show_config` command to display mappings and monitored bot IDs.
- Improved debugging for commands.
"""

import json
import logging
from pathlib import Path
from typing import Optional, List

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger(__name__)

DATA_DIR = Path("data")
BUILDABLES_DEF_FILE = DATA_DIR / "buildables.json"


def _load_buildables() -> dict:
    """Load buildables definitions from JSON file."""
    try:
        if BUILDABLES_DEF_FILE.exists():
            with BUILDABLES_DEF_FILE.open("r", encoding="utf-8") as fp:
                return json.load(fp)
    except Exception:
        logger.exception("Failed to load buildables definitions.")
    return {}


async def _autocomplete_buildable_parts(interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
    """Autocomplete handler for buildable parts."""
    buildables = _load_buildables()
    parts = []
    for buildable, attributes in buildables.items():
        for part in attributes.get("parts", {}):
            entry = f"{buildable}:{part}"
            if current.lower() in entry.lower():
                parts.append(app_commands.Choice(name=entry, value=entry))
                if len(parts) >= 25:
                    break
    return parts


class RumbleAdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="rumble_set_channel_part", description="Set channel mapping for a part.")
    @commands.has_permissions(manage_guild=True)
    @app_commands.autocomplete(selection=_autocomplete_buildable_parts)
    async def rumble_set_channel_part(self, ctx: commands.Context, selection: str, channel: Optional[discord.TextChannel] = None):
        """Set channel-to-part mappings and persist configuration."""
        listener = self.bot.get_cog("RumbleListenerCog")
        if not listener or not hasattr(listener, "channel_part_map"):
            await ctx.reply("RumbleListenerCog is not available.", ephemeral=True)
            return

        if ":" not in selection:
            await ctx.reply("Selection must be in the format '<buildable>:<part>'.", ephemeral=True)
            return

        buildable, part = selection.split(":")
        buildables = _load_buildables()
        if buildable not in buildables or part not in buildables[buildable].get("parts", {}):
            await ctx.reply(f"Invalid selection: {selection}. Check buildables.json.", ephemeral=True)
            return

        target_channel = channel or ctx.channel
        listener.channel_part_map[int(target_channel.id)] = (buildable, part)
        listener._save_config_file()  # Persist mapping
        await ctx.reply(f"Mapping for channel {target_channel.mention} updated: {selection}.", ephemeral=True)
        logger.info("Updated mapping: Channel %s -> %s:%s", target_channel.id, buildable, part)

    @commands.hybrid_command(name="rumble_show_config", description="Show current configuration for RumbleListener.")
    async def rumble_show_config(self, ctx: commands.Context):
        """Display the current configuration for the RumbleListener."""
        listener = self.bot.get_cog("RumbleListenerCog")
        if not listener or not hasattr(listener, "channel_part_map"):
            await ctx.reply("RumbleListenerCog is not available.", ephemeral=True)
            return

        try:
            monitored_bots = listener.rumble_bot_ids or []
            channel_map = listener.channel_part_map

            # Generate readable config output
            config_text = "Monitored Bot IDs:\n"
            config_text += "\n".join(f"- {bot_id}" for bot_id in monitored_bots) if monitored_bots else "None"

            config_text += "\n\nChannel Part Mappings:\n"
            if channel_map:
                for ch_id, (buildable, part) in channel_map.items():
                    config_text += f"- Channel {ch_id}: {buildable} -> {part}\n"
            else:
                config_text += "None"

            await ctx.reply(f"```\n{config_text}\n```", ephemeral=True)
            logger.info("Displayed configuration to %s", ctx.author.id)
        except Exception:
            logger.exception("Failed to display configuration.")
            await ctx.reply("Failed to load current configuration. Check logs or persist file.", ephemeral=True)

    @commands.hybrid_command(name="rumble_remove_channel", description="Remove channel mapping.")
    @commands.has_permissions(manage_guild=True)
    async def rumble_remove_channel(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        """Remove part mapping for the specified channel."""
        listener = self.bot.get_cog("RumbleListenerCog")
        if not listener or not hasattr(listener, "channel_part_map"):
            await ctx.reply("RumbleListenerCog is not available.", ephemeral=True)
            return

        target_channel = channel or ctx.channel
        if int(target_channel.id) in listener.channel_part_map:
            del listener.channel_part_map[int(target_channel.id)]
            listener._save_config_file()
            await ctx.reply(f"Removed mapping for channel {target_channel.mention}.", ephemeral=True)
            logger.info("Removed mapping for channel %s", target_channel.id)
        else:
            await ctx.reply(f"No mapping exists for channel {target_channel.mention}.", ephemeral=True)

    @commands.hybrid_command(name="rumble_test_award", description="Simulate awarding a part to a user.")
    @commands.has_permissions(manage_guild=True)
    async def rumble_test_award(self, ctx: commands.Context, member: discord.Member, channel_id: Optional[str] = None):
        """Simulates awarding a part to a user in the specified channel."""
        listener = self.bot.get_cog("RumbleListenerCog")
        if not listener or not hasattr(listener, "channel_part_map"):
            await ctx.reply("RumbleListenerCog is not available.", ephemeral=True)
            return

        target_channel = self.bot.get_channel(int(channel_id)) if channel_id else ctx.channel
        mapping = listener.channel_part_map.get(int(target_channel.id))
        if not mapping:
            await ctx.reply(f"No mapping exists for channel {target_channel.mention}.", ephemeral=True)
            return

        buildable, part = mapping
        stocking = self.bot.get_cog("StockingCog")
        if not stocking or not hasattr(stocking, "award_part"):
            await ctx.reply("StockingCog is not available.", ephemeral=True)
            return

        try:
            await stocking.award_part(member.id, buildable, part, target_channel, announce=False)
            logger.info("Test awarded part '%s' for buildable '%s' to user %s", part, buildable, member.id)
            await ctx.reply(f"Test award successful: {member.mention} received '{part}' for '{buildable}'.", ephemeral=True)
        except Exception:
            logger.exception("Failed to test award.")
            await ctx.reply("Failed to test award due to an exception. See logs.", ephemeral=True)

    @commands.hybrid_command(name="rumble_give_part", description="Give a part to a user.")
    @commands.has_permissions(manage_guild=True)
    @app_commands.autocomplete(selection=_autocomplete_buildable_parts)
    async def rumble_give_part(self, ctx: commands.Context, member: discord.Member, selection: str):
        """Manually give a part to a user."""
        stocking = self.bot.get_cog("StockingCog")
        if not stocking or not hasattr(stocking, "award_part"):
            await ctx.reply("StockingCog is not available.", ephemeral=True)
            return

        if ":" not in selection:
            await ctx.reply("Selection must be in the format '<buildable>:<part>'.", ephemeral=True)
            return

        buildable, part = selection.split(":")
        buildables = _load_buildables()
        if buildable not in buildables or part not in buildables[buildable].get("parts", {}):
            await ctx.reply(f"Invalid selection: {selection}. Check buildables.json.", ephemeral=True)
            return

        try:
            await stocking.award_part(member.id, buildable, part, None, announce=False)
            logger.info("Given part '%s' for buildable '%s' to user %s", part, buildable, member.id)
            await ctx.reply(f"Successfully gave '{part}' for '{buildable}' to {member.mention}.", ephemeral=True)
        except Exception:
            logger.exception("Failed to give part.")
            await ctx.reply("Failed to give part due to an exception. See logs.", ephemeral=True)

    @commands.hybrid_command(name="rumble_take_part", description="Remove a part from a user.")
    @commands.has_permissions(manage_guild=True)
    @app_commands.autocomplete(selection=_autocomplete_buildable_parts)
    async def rumble_take_part(self, ctx: commands.Context, member: discord.Member, selection: str):
        """Manually remove a part from a user."""
        stocking = self.bot.get_cog("StockingCog")
        if not stocking or not hasattr(stocking, "remove_part"):
            await ctx.reply("StockingCog is not available.", ephemeral=True)
            return

        if ":" not in selection:
            await ctx.reply("Selection must be in the format '<buildable>:<part>'.", ephemeral=True)
            return

        buildable, part = selection.split(":")
        try:
            await stocking.remove_part(member.id, buildable, part)
            logger.info("Removed part '%s' for buildable '%s' from user %s", part, buildable, member.id)
            await ctx.reply(f"Successfully removed '{part}' for '{buildable}' from {member.mention}.", ephemeral=True)
        except Exception:
            logger.exception("Failed to remove part.")
            await ctx.reply("Failed to remove part due to an exception. See logs.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(RumbleAdminCog(bot))