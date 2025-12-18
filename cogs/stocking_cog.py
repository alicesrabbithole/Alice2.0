#!/usr/bin/env python3
"""
Updated StockingCog:
- Role assignment moved to `/mysnowman`.
- Handles stocking data persistence.
- Manage parts, buildable progress, and completion roles.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)

DATA_DIR = Path("data")
STOCKINGS_FILE = DATA_DIR / "stockings.json"
BUILDABLES_DEF_FILE = DATA_DIR / "buildables.json"
DEFAULT_CAPACITY = 12
AUTO_ROLE_ID = 1448857904282206208


def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


class StockingCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        ensure_dirs()
        self._data: Dict[str, Dict[str, Any]] = {}
        self._buildables_def: Dict[str, Dict[str, Any]] = {}
        self._load_data()

    def _load_data(self):
        """Load stocking and buildables data."""
        try:
            if STOCKINGS_FILE.exists():
                with STOCKINGS_FILE.open("r", encoding="utf-8") as fh:
                    self._data = json.load(fh)
            else:
                self._data = {}
        except Exception:
            logger.exception("Failed to load stockings data.")

        try:
            if BUILDABLES_DEF_FILE.exists():
                with BUILDABLES_DEF_FILE.open("r", encoding="utf-8") as fh:
                    self._buildables_def = json.load(fh)
            else:
                self._buildables_def = {}
        except Exception:
            logger.exception("Failed to load buildables definitions.")

    async def _save_data(self):
        """Persist stocking data to disk."""
        try:
            STOCKINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with STOCKINGS_FILE.open("w", encoding="utf-8") as fh:
                json.dump(self._data, fh, ensure_ascii=False, indent=2)
        except Exception:
            logger.exception("Failed to save stockings data.")

    async def mysnowman(self, ctx: commands.Context):
        """Show the user's assembled snowman and assign role if completed."""
        user_id = ctx.author.id
        user = self._data.get(str(user_id), {"buildables": {}})
        buildable_key = "snowman"
        buildable = user.get("buildables", {}).get(buildable_key, {"parts": [], "completed": False})

        parts_collected = len(buildable.get("parts", []))
        required_parts = self._buildables_def.get(buildable_key, {}).get("capacity_slots", 7)

        # Check for snowman completion
        if parts_collected >= required_parts and not buildable.get("completed"):
            buildable["completed"] = True
            await self._save_data()
            await self._grant_role(ctx.author, ctx.guild, buildable_key)
            await ctx.reply(f"🎉 Your snowman is complete! You've been awarded the role!")

        # Respond with buildable status
        await ctx.reply(f"Your snowman parts: {parts_collected}/{required_parts}. Keep collecting!")

    async def _grant_role(self, member: discord.Member, guild: discord.Guild, buildable_key: str):
        """Grant completion role for the snowman."""
        role_id = self._buildables_def.get(buildable_key, {}).get("role_on_complete", AUTO_ROLE_ID)
        role = guild.get_role(role_id)

        if role and role not in member.roles:
            try:
                await member.add_roles(role, reason=f"{buildable_key} completed")
                logger.info(f"Granted role {role.name} for {buildable_key} completion to {member.name}")
            except Exception:
                logger.exception(f"Failed to grant role {role_id} to {member.id}")

    async def award_part(self, user_id: int, buildable_key: str, part_key: str, channel: Optional[discord.TextChannel] = None, *, announce: bool = True) -> bool:
        """Award a part to a user."""
        build_def = self._buildables_def.get(buildable_key)
        if not build_def:
            logger.warning(f"Buildable {buildable_key} not found.")
            return False

        parts_def = build_def.get("parts", {})
        if part_key not in parts_def:
            logger.warning(f"Part {part_key} not defined for buildable {buildable_key}.")
            return False

        user = self._data.setdefault(str(user_id), {"buildables": {}})
        buildable = user["buildables"].setdefault(buildable_key, {"parts": [], "completed": False})

        if part_key in buildable["parts"]:
            logger.info(f"User {user_id} already has part {part_key} for {buildable_key}.")
            return False

        buildable["parts"].append(part_key)
        await self._save_data()

        # Send announcement if applicable
        if announce and channel:
            mention = f"<@{user_id}>"
            try:
                member = channel.guild.get_member(user_id)
                if member:
                    mention = member.mention
            except Exception:
                logger.warning("Failed to get member mention.")

            embed = discord.Embed(
                title=f"Part Awarded — {buildable_key}",
                description=f"🎉 {mention} received the **{part_key}** for **{buildable_key}**!",
                color=discord.Color.green(),
            )
            try:
                await channel.send(embed=embed)
            except Exception:
                logger.exception("Failed to send part award message.")

        return True

    async def remove_part(self, user_id: int, buildable_key: str, part_key: str) -> bool:
        """Remove a part from a user's buildable."""
        user = self._data.get(str(user_id), {"buildables": {}})
        buildable = user.get("buildables", {}).get(buildable_key)
        if not buildable:
            return False

        if part_key not in buildable["parts"]:
            return False

        buildable["parts"].remove(part_key)
        await self._save_data()
        return True

    async def revoke_part(self, user_id: int, buildable_key: str, part_key: str) -> bool:
        """Alias for remove_part."""
        return await self.remove_part(user_id, buildable_key, part_key)


async def setup(bot: commands.Bot):
    await bot.add_cog(StockingCog(bot))