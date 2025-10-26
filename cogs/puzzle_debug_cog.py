import discord
from discord.ext import commands
from discord import app_commands
from tools.patch_config import patch_config  # adjust path if needed

class PuzzleDebug(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(
        name="refreshconfig",
        description="Re-patch puzzle config from folders",
        extras={"category": "Debug", "owner": True}
    )
    @commands.is_owner()
    async def refreshconfig(self, ctx: commands.Context):
        try:
            patch_config("config.json", project_anchor=__file__)
            await ctx.reply("✅ Puzzle config refreshed from disk.", ephemeral=False)
        except Exception as e:
            await ctx.reply(f"❌ Failed to refresh config: {e}", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(PuzzleDebug(bot))
