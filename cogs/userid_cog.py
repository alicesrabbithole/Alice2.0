import discord
from discord.ext import commands
from discord import app_commands

import config


class UseridCog(commands.Cog, name="User Utilities"):
    """A simple cog to retrieve a user's Discord ID."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="id", description="Get a member's user ID.")
    @app_commands.describe(member="The member you want the ID of (optional, defaults to you).")
    @app_commands.guild_only()
    async def id(self, ctx: commands.Context, member: discord.Member = None):
        """Shows the Discord ID for yourself or another member."""
        # This command can be useful for anyone, so we don't restrict it by guild.
        target = member or ctx.author

        embed = discord.Embed(
            description=f"The ID for {target.mention} is:",
            color=target.color if hasattr(target, 'color') else discord.Color.blue()
        )
        embed.set_author(name=f"{target.display_name}'s ID", icon_url=target.display_avatar.url)
        embed.add_field(name="User ID", value=f"```{target.id}```")

        await ctx.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(UseridCog(bot))