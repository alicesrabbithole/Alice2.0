#!/usr/bin/env python3
"""
Refactored RumbleAdminCog to include:
- Persistent channel mappings via `rumble_set_channel_part`.
- Improved debugging for commands.
- Expanded autocomplete for parts/buildables.
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


async def setup(bot: commands.Bot):
    await bot.add_cog(RumbleAdminCog(bot))