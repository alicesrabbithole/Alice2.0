import discord
from discord.ext import commands

# Import your theme and new checks
import config
from utils.checks import is_staff
from utils.theme import Emojis, Colors

from config import OWNER_ID

def _to_int(x):
    try:
        return int(x)
    except Exception:
        return None

owner_id = _to_int(OWNER_ID) if 'OWNER_ID' in globals() else None

bot = commands.Bot(
    command_prefix="!",  # your prefix
    intents=...,        # your intents
    owner_id=owner_id,  # single owner
)

class ModerationCog(commands.Cog, name="Moderation"):
    """Commands for server moderation."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="lock", description="🔒 Lock the current channel.")
    @commands.guild_only()
    @is_staff()
    async def lock(self, ctx: commands.Context):
        """Locks the current channel."""
        channel = ctx.channel
        verified_role = ctx.guild.get_role(config.VERIFIED_ROLE_ID)

        if not verified_role:
            await ctx.send(f"{Emojis.FAILURE} Tell Alice that the verified role for unlock/lock is broken", ephemeral=True)
            return

        overwrites = channel.overwrites_for(verified_role)
        if overwrites.send_messages is False:
            await ctx.send(f"{Emojis.LOCK} This channel is already locked.", ephemeral=True)
            return

        overwrites.send_messages = False
        try:
            await channel.set_permissions(verified_role, overwrite=overwrites, reason=f"Channel locked by {ctx.author}")
            embed = discord.Embed(description=f"{Emojis.LOCK} This channel has been **locked**.", color=Colors.THEME_COLOR)
            await ctx.send(embed=embed)
        except discord.Forbidden:
            await ctx.send(f"{Emojis.FAILURE} Tell Alice that I can't do my job in this channel .", ephemeral=True)

    @commands.hybrid_command(name="unlock", description="🔓 Unlock the current channel.")
    @commands.guild_only()
    @is_staff()
    async def unlock(self, ctx: commands.Context):
        """Unlocks the current channel."""
        channel = ctx.channel
        verified_role = ctx.guild.get_role(config.VERIFIED_ROLE_ID)

        if not verified_role:
            await ctx.send(f"{Emojis.FAILURE} Tell Alice that the verified role for unlock/lock is broken", ephemeral=True)
            return

        overwrites = channel.overwrites_for(verified_role)
        if overwrites.send_messages is True:
            await ctx.send(f"{Emojis.UNLOCK} This channel is already unlocked.", ephemeral=True)
            return

        overwrites.send_messages = True
        try:
            await channel.set_permissions(verified_role, overwrite=overwrites,
                                          reason=f"Channel unlocked by {ctx.author}")
            embed = discord.Embed(description=f"{Emojis.UNLOCK} This channel has been **unlocked**.", color=Colors.THEME_COLOR)
            await ctx.send(embed=embed)
        except discord.Forbidden:
            await ctx.send(f"{Emojis.Failure} Tell Alice that I can't do my job in this channel .", ephemeral=True)

    @commands.hybrid_command(name="addrole", description="Add a role to a member.")
    @commands.guild_only()
    @is_staff()
    async def addrole(self, ctx: commands.Context, role: discord.Role, member: discord.Member):
        """Adds a role to a member."""
        if role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
            await ctx.send(f"{Emojis.FAILURE} You cannot manage roles that are higher than or equal to your own.", ephemeral=True)
            return
        if role >= ctx.guild.me.top_role:
            await ctx.send(f"{Emojis.FAILURE} I cannot manage that role because it is higher than or equal to my own.", ephemeral=True)
            return

        try:
            await member.add_roles(role, reason=f"Role added by {ctx.author}")
            await ctx.send(f"{Emojis.SUCCESS} Added {role.mention} to {member.mention}.", ephemeral=False)
        except discord.Forbidden:
            await ctx.send(f"{Emojis.FAILURE} Tell Alice to fix my permissions.", ephemeral=False)

    @commands.hybrid_command(name="removerole", description="Remove a role from a member.")
    @commands.guild_only()
    @is_staff()
    async def removerole(self, ctx: commands.Context, role: discord.Role, member: discord.Member):
        """Removes a role from a member."""
        if role >= ctx.author.top_role and ctx.author != ctx.guild.owner:
            await ctx.send(f"{Emojis.FAILURE} You cannot manage roles that are higher than or equal to your own.", ephemeral=True)
            return
        if role >= ctx.guild.me.top_role:
            await ctx.send(f"{Emojis.FAILURE} I cannot manage that role because it is higher than or equal to my own.", ephemeral=True)
            return

        try:
            await member.remove_roles(role, reason=f"Role removed by {ctx.author}")
            await ctx.send(f"{Emojis.SUCCESS} Removed {role.mention} from {member.mention}.", ephemeral=False)
        except discord.Forbidden:
            await ctx.send(f"{Emojis.FAILURE} Tell Alice to fix my permissions.", ephemeral=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(ModerationCog(bot))