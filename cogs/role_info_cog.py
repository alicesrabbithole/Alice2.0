import discord
from discord.ext import commands
from discord import app_commands


class RoleInfoCog(commands.Cog):
    """A cog to display detailed information about a server role."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="roleinfo", description="Show detailed information about a role.")
    @app_commands.describe(role="The role you want information about.")
    @commands.guild_only()
    @commands.has_permissions(manage_roles=True)  # Ensures only users who can manage roles can use it
    async def roleinfo(self, ctx: commands.Context, role: discord.Role):
        embed = discord.Embed(
            title=f"🔍 Role Info: {role.name}",
            color=role.color if role.color.value != 0 else discord.Color.light_grey()
        )
        embed.add_field(name="Role ID", value=f"`{role.id}`", inline=True)
        embed.add_field(name="Members", value=str(len(role.members)), inline=True)
        embed.add_field(name="Color (Hex)", value=f"`{str(role.color)}`", inline=True)

        embed.add_field(name="Mentionable", value="✅ Yes" if role.mentionable else "❌ No", inline=True)
        embed.add_field(name="Hoisted", value="✅ Yes" if role.hoist else "❌ No", inline=True)
        embed.add_field(name="Bot Role", value="✅ Yes" if role.is_bot_managed() else "❌ No", inline=True)

        embed.set_footer(text=f"Guild: {ctx.guild.name}", icon_url=ctx.guild.icon.url if ctx.guild.icon else None)

        await ctx.send(embed=embed)

    @roleinfo.error
    async def roleinfo_error(self, ctx: commands.Context, error):
        """Handle errors for the roleinfo command."""
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ You don't have the `Manage Roles` permission to use this command.", ephemeral=True)
        elif isinstance(error, commands.RoleNotFound):
            await ctx.send(f"❌ I couldn't find a role named `{error.argument}`.", ephemeral=True)
        else:
            await ctx.send("An unexpected error occurred. Please try again later.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(RoleInfoCog(bot))