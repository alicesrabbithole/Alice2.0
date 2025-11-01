import json
import discord
from discord.ext import commands
from discord import app_commands
from typing import List

import config


# --- Permission Management Functions ---

def load_permissions() -> dict:
    """Loads permissions from the JSON file."""
    try:
        with open(config.PERMISSIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        # If file is missing or broken, return a default structure
        return {"moderator": [], "puzzle_master": []}


def save_permissions(perms: dict):
    """Saves the permissions dictionary to the JSON file."""
    config.DATA_DIR.mkdir(exist_ok=True)
    with open(config.PERMISSIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(perms, f, indent=4)


# --- The Custom Check ---

def can_use(permission_group: str):
    """
    A decorator/check that verifies if a user has a role with the required permission.
    Permission groups are 'moderator' and 'puzzle_master'.
    """

    async def predicate(ctx: commands.Context) -> bool:
        # Bot owner and server administrators can always use any command.
        if await ctx.bot.is_owner(ctx.author) or (
                ctx.guild and ctx.author.guild_permissions.administrator):
            return True

        # Check for the required permission group.
        perms = load_permissions()
        allowed_role_ids = set(perms.get(permission_group, []))
        user_role_ids = {role.id for role in ctx.author.roles}

        if not user_role_ids.isdisjoint(allowed_role_ids):
            return True

        # If we're here, the user does not have permission.
        # Send an ephemeral message to avoid clutter.
        await ctx.send(f"❌ You don't have a role with the `{permission_group}` permission to use this command.",
                       ephemeral=True)
        return False

    return commands.check(predicate)


class PermissionsCog(commands.Cog, name="Permissions"):
    """Manages the bot's role-based permission system."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    perms_group = app_commands.Group(
        name="perms",
        description="Manage bot permissions for roles.",
        guild_ids=[config.WONDERLAND_GUILD_ID]
    )

    @perms_group.command(name="grant", description="Grant a permission group to a role.")
    @app_commands.checks.has_permissions(administrator=True)
    async def perms_grant(self, interaction: discord.Interaction, role: discord.Role,
                          group: str):  # <<< FIX IS HERE
        """Grants a permission group to a role."""
        perms = load_permissions()

        # The value from the autocomplete is passed directly.
        if group not in perms:
            await interaction.response.send_message(
                f"❌ Invalid permission group. Use one of: `{', '.join(perms.keys())}`", ephemeral=True)
            return

        if role.id in perms[group]:
            await interaction.response.send_message(f"⚠️ {role.mention} already has the `{group}` permission.",
                                                    ephemeral=True)
            return

        perms[group].append(role.id)
        save_permissions(perms)
        await interaction.response.send_message(f"✅ Granted permission group `{group}` to {role.mention}.",
                                                ephemeral=True)

    @perms_grant.autocomplete("group")
    async def group_autocomplete(self, interaction: discord.Interaction, current: str) -> List[
        app_commands.Choice[str]]:
        groups = ["moderator", "puzzle_master"]
        return [
            app_commands.Choice(name=group.replace("_", " ").title(), value=group)
            for group in groups if current.lower() in group.lower()
        ]

    @perms_group.command(name="revoke", description="Revoke a permission group from a role.")
    @app_commands.checks.has_permissions(administrator=True)
    async def perms_revoke(self, interaction: discord.Interaction, role: discord.Role,
                           group: str):  # <<< FIX IS HERE
        """Revokes a permission group from a role."""
        perms = load_permissions()

        if group not in perms:
            await interaction.response.send_message(
                f"❌ Invalid permission group. Use one of: `{', '.join(perms.keys())}`", ephemeral=True)
            return

        if role.id not in perms[group]:
            await interaction.response.send_message(f"⚠️ {role.mention} does not have the `{group}` permission.",
                                                    ephemeral=True)
            return

        perms[group].remove(role.id)
        save_permissions(perms)
        await interaction.response.send_message(f"✅ Revoked permission group `{group}` from {role.mention}.",
                                                ephemeral=True)

    @perms_revoke.autocomplete("group")
    async def group_autocomplete_revoke(self, interaction: discord.Interaction, current: str) -> List[
        app_commands.Choice[str]]:
        return await self.group_autocomplete(interaction, current)

    @perms_group.command(name="list", description="List all roles assigned to permission groups.")
    @app_commands.checks.has_permissions(administrator=True)
    async def perms_list(self, interaction: discord.Interaction):
        """Lists all current permission assignments."""
        perms = load_permissions()
        embed = discord.Embed(title="Bot Permission Assignments", color=discord.Color.blue())

        if not any(perms.values()):
            embed.description = "No permissions have been assigned yet."
        else:
            for group, role_ids in perms.items():
                if role_ids:
                    # Fetch role mentions, handling cases where a role might have been deleted.
                    value = "\n".join(
                        f"<@&{rid}>" for rid in role_ids if interaction.guild.get_role(rid)
                    ) or "No roles assigned."
                    embed.add_field(name=f"👑 {group.replace('_', ' ').title()}", value=value, inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(PermissionsCog(bot))