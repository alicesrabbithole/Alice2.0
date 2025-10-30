import discord
from discord.ext import commands
from discord import app_commands, Interaction

class HelpCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="alicehelp", aliases=["help"], description="Shows a list of all available commands.")
    async def alicehelp(self, ctx: commands.Context):
        embed = discord.Embed(title="Alice 2.0 Help", color=discord.Color.purple()).set_thumbnail(url=self.bot.user.display_avatar.url)
        user_commands = "`/viewpuzzle <puzzle_name>`\n`/leaderboard <puzzle_name>`\n`/alicehelp` or `!help`"
        embed.add_field(name="✨ User Commands", value=user_commands, inline=False)
        staff_commands = "`/spawndrop <puzzle> [channel]`\n`/syncpuzzles`\n`/listdropsettings`\n`/listclaims`"
        embed.add_field(name="👑 Staff Commands", value=staff_commands, inline=False)
        admin_commands = "`/setdropchannel <channel> <puzzle> [mode] [value]`\n`/removedropchannel <channel>`\n`/addstaff <user>`\n`/removestaff <user>`\n`/testlog`"
        embed.add_field(name="🛡️ Admin Commands", value=admin_commands, inline=False)
        owner_commands = "`/reload`\n`/setclaimrange <channel> <min> <max>`\n`/givepiece <user> <puzzle> <id>`\n`/takepiece <user> <puzzle> <id>`\n`/wipepuzzle <puzzle>`"
        embed.add_field(name="OWNER ONLY", value=owner_commands, inline=False)
        embed.set_footer(text="Thank you for playing! You can use ! or / for any command.")
        await ctx.send(embed=embed, ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(HelpCog(bot))