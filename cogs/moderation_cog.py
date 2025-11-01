import discord
from discord.ext import commands
from discord import app_commands

import config
from .permissions_cog import can_use


class ModerationCog(commands.Cog, name="Moderation"):
    """Commands for server moderation, like managing roles and channels."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_check(self, ctx: commands.Context) -> bool:
        """A general check for all commands in this cog to ensure they're in the right server."""
        if ctx.guild is None or ctx.guild.id != config.WONDERLAND_GUILD_ID:
            # This check now silently fails for commands in the wrong server.
            # The `can_use` decorator will provide a user-facing message if needed.
            return False
        return True

    @commands.hybrid_command(name="lock", description="🔒 Lock the current channel for verified members.")
    @can_use("moderator")
    async def lock(self, ctx: commands.Context):
        """Locks the current channel."""
        await self._update_channel_permissions(ctx, lock=True)

    @commands.hybrid_command(name="unlock", description="🔓 Unlock the current channel for verified members.")
    @can_use("moderator")
    async def unlock(self, ctx: commands.Context):
        """Unlocks the current channel."""
        await self._update_channel_permissions(ctx, lock=False)

    async def _update_channel_permissions(self, ctx: commands.Context, lock: bool):
        """Helper function to modify channel permissions for the verified role."""
        channel = ctx.channel
        verified_role = ctx.guild.get_role(config.VERIFIED_ROLE_ID)

        if not verified_role:
            await ctx.send("❌ Could not find the `@Verified` role. Please check the role ID in `config.py`.",
                           ephemeral=True)
            return

        overwrites = channel.overwrites_for(verified_role)
        overwrites.send_messages = not lock

        try:
            await channel.set_permissions(verified_role, overwrite=overwrites,
                                          reason=f"Channel {'locked' if lock else 'unlocked'} by {ctx.author}")
            emoji = "🔒" if lock else "🔓"
            embed = discord.Embed(
                description=f"{emoji} This channel has been **{'locked' if lock else 'unlocked'}**.",
                color=discord.Color.dark_red() if lock else discord.Color.green()
            )
            await ctx.send(embed=embed)
        except discord.Forbidden:
            await ctx.send("❌ I don't have the `Manage Channels` permission to do that.", ephemeral=True)

    @commands.hybrid_command(name="addrole", description="Add a role to a member.")
    @app_commands.describe(role="The role to add.", member="The member to give the role to.")
    @can_use("moderator")
    async def addrole(self, ctx: commands.Context, role: discord.Role, member: discord.Member):
        """Adds a role to a member."""
        if role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
            return await ctx.send("❌ You cannot manage roles that are higher than or equal to your own.",
                                  ephemeral=False)
        if role >= ctx.guild.me.top_role:
            return await ctx.send("❌ I cannot manage that role because it is higher than or equal to my own.",
                                  ephemeral=False)

        try:
            await member.add_roles(role, reason=f"Role added by {ctx.author}")
            await ctx.send(f"✅ Added {role.mention} to {member.mention}.", ephemeral=False)
        except discord.Forbidden:
            await ctx.send("❌ I don't have the necessary permissions to add that role.", ephemeral=False)

    @commands.hybrid_command(name="removerole", description="Remove a role from a member.")
    @app_commands.describe(role="The role to remove.", member="The member to remove the role from.")
    @can_use("moderator")
    async def removerole(self, ctx: commands.Context, role: discord.Role, member: discord.Member):
        """Removes a role from a member."""
        if role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
            return await ctx.send("❌ You cannot manage roles that are higher than or equal to your own.",
                                  ephemeral=False)
        if role >= ctx.guild.me.top_role:
            return await ctx.send("❌ I cannot manage that role because it is higher than or equal to my own.",
                                  ephemeral=False)

        try:
            await member.remove_roles(role, reason=f"Role removed by {ctx.author}")
            await ctx.send(f"✅ Removed {role.mention} from {member.mention}.", ephemeral=False)
        except discord.Forbidden:
            await ctx.send("❌ I don't have the necessary permissions to remove that role.", ephemeral=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(ModerationCog(bot))