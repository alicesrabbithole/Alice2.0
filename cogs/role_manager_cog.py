import discord
from discord.ext import commands
from discord import app_commands

# --- Constants ---
WONDERLAND_GUILD_ID = 1309962372269609010
MODERATOR_ROLE_ID = 1309962372542234657  # The role required to use these commands


class RoleManagerCog(commands.Cog):
    """A cog for adding and removing roles from members."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # This check runs for every command in this cog
    async def cog_check(self, ctx: commands.Context) -> bool:
        if ctx.guild is None or ctx.guild.id != WONDERLAND_GUILD_ID:
            await ctx.send("❌ This command can only be used in the **Wonderland** server.", ephemeral=True)
            return False

        # Check if the user has the required role
        required_role = ctx.guild.get_role(MODERATOR_ROLE_ID)
        if required_role is None or required_role not in ctx.author.roles:
            await ctx.send(
                f"❌ You need the {required_role.mention if required_role else 'moderator'} role to use this command.",
                ephemeral=True)
            return False

        return True

    @commands.hybrid_command(name="addrole", description="Add a role to a member.")
    @app_commands.describe(role="The role to add.", member="The member to give the role to.")
    async def addrole(self, ctx: commands.Context, role: discord.Role, member: discord.Member):
        try:
            await member.add_roles(role, reason=f"Role added by {ctx.author}")
            await ctx.send(f"✅ Added {role.mention} to {member.mention}.", ephemeral=True)
        except discord.Forbidden:
            await ctx.send("❌ I don't have the necessary permissions to add that role. My role might be too low.",
                           ephemeral=True)
        except discord.HTTPException:
            await ctx.send("❌ An error occurred while trying to add the role.", ephemeral=True)

    @commands.hybrid_command(name="removerole", description="Remove a role from a member.")
    @app_commands.describe(role="The role to remove.", member="The member to remove the role from.")
    async def removerole(self, ctx: commands.Context, role: discord.Role, member: discord.Member):
        try:
            await member.remove_roles(role, reason=f"Role removed by {ctx.author}")
            await ctx.send(f"✅ Removed {role.mention} from {member.mention}.", ephemeral=True)
        except discord.Forbidden:
            await ctx.send("❌ I don't have the necessary permissions to remove that role. My role might be too low.",
                           ephemeral=True)
        except discord.HTTPException:
            await ctx.send("❌ An error occurred while trying to remove the role.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(RoleManagerCog(bot))