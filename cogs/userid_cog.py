import discord
from discord.ext import commands
from discord import app_commands

# --- Constants ---
WONDERLAND_GUILD_ID = 1309962372269609010

class UseridCog(commands.Cog):
    """A simple cog to retrieve a user's Discord ID."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="id", description="Get a member's user ID.")
    @app_commands.describe(member="The member you want the ID of (optional, defaults to you).")
    async def id(self, ctx: commands.Context, member: discord.Member = None):
        # Ensure the command is used in the correct server
        if ctx.guild is None or ctx.guild.id != WONDERLAND_GUILD_ID:
            await ctx.send("❌ This command can only be used in the **Wonderland** server.", ephemeral=True)
            return

        # If no member is specified, the target is the person who ran the command
        target = member or ctx.author

        embed = discord.Embed(
            title="🆔 User ID",
            description=f"The ID for {target.mention} is:",
            color=discord.Color.blue()
        )
        embed.add_field(name="User ID", value=f"```{target.id}```", inline=False)
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.set_footer(text=f"Requested by {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)

        await ctx.send(embed=embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(UseridCog(bot))