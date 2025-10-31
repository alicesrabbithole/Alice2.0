import discord
from discord.ext import commands
from discord import app_commands

# --- Constants ---
WONDERLAND_GUILD_ID = 1309962372269609010
VERIFIED_ROLE_ID = 1309967307149148192  # The role to lock/unlock the channel for


class LockUnlockCog(commands.Cog):
    """A cog to lock and unlock a channel for a specific role."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _update_channel_permissions(self, ctx: commands.Context, lock: bool):
        """Helper function to modify channel permissions."""
        channel = ctx.channel
        verified_role = ctx.guild.get_role(VERIFIED_ROLE_ID)

        if not verified_role:
            await ctx.send("❌ Could not find the `@Verified` role. Please check the role ID.", ephemeral=True)
            return

        try:
            # Get the current permissions for the role in the channel, or create new ones
            overwrites = channel.overwrites_for(verified_role)

            # Set send_messages to False to lock, True to unlock
            overwrites.send_messages = not lock

            await channel.set_permissions(verified_role, overwrite=overwrites,
                                          reason=f"Channel {'locked' if lock else 'unlocked'} by {ctx.author}")

            emoji = "<:lockaiw:1328747936204591174>" if lock else "<:key_aiw:1328742847456874565>"
            embed = discord.Embed(
                description=f"{emoji} This channel has been **{'locked' if lock else 'unlocked'}**.",
                color=discord.Color.dark_red() if lock else discord.Color.green()
            )
            await ctx.send(embed=embed)

        except discord.Forbidden:
            await ctx.send("❌ I don't have the `Manage Roles` or `Manage Channels` permission to do that.",
                           ephemeral=True)
        except Exception as e:
            await ctx.send(f"An unexpected error occurred: {e}", ephemeral=True)

    @commands.hybrid_command(name="lock", description="🔒 Lock the current channel for verified members.")
    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    async def lock(self, ctx: commands.Context):
        # Ensure it's used in the right server before proceeding
        if ctx.guild.id != WONDERLAND_GUILD_ID:
            await ctx.send("❌ This command can only be used in the **Wonderland** server.", ephemeral=True)
            return
        await self._update_channel_permissions(ctx, lock=True)

    @commands.hybrid_command(name="unlock", description="🔓 Unlock the current channel for verified members.")
    @commands.guild_only()
    @commands.has_permissions(manage_channels=True)
    async def unlock(self, ctx: commands.Context):
        # Ensure it's used in the right server before proceeding
        if ctx.guild.id != WONDERLAND_GUILD_ID:
            await ctx.send("❌ This command can only be used in the **Wonderland** server.", ephemeral=True)
            return
        await self._update_channel_permissions(ctx, lock=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(LockUnlockCog(bot))