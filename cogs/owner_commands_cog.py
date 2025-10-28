import discord
from discord.ext import commands
import os
import asyncio
from discord import app_commands
import platform
import psutil
import json
import datetime
from cogs.db_utils import get_drop_channels, slugify_key
import logging
logger = logging.getLogger(__name__)

logger.warning("🧪 [COG NAME] loaded")

GUILD_ID = 1309962372269609010

# --- module-level autocomplete ---
async def cog_name_autocomplete(interaction: discord.Interaction, current: str):
    return [
        app_commands.Choice(
            name=f"{ext} ✅" if ext in interaction.client.extensions else f"{ext} ❌",
            value=ext
        )
        for ext in interaction.client.extensions
        if current.lower() in ext.lower()
    ][:25]

class OwnerCommandsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="listowner", extras={"category": "Owner", "owner": True})
    @commands.is_owner()
    async def listowner(self, ctx: commands.Context):
        cmds = [cmd for cmd in self.bot.commands if cmd.extras.get("owner")]
        if not cmds:
            await ctx.send("No owner-only commands found.")
            return

        lines = []
        for cmd in sorted(cmds, key=lambda c: c.name):
            marker = "/" if isinstance(cmd, commands.HybridCommand) else "!"
            lines.append(f"{marker}{cmd.name} — {cmd.short_doc or 'No description'}")

        embed = discord.Embed(
            title="🔒 Owner Commands",
            description="\n".join(lines),
            color=discord.Color.dark_gold()
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="togglecog",
        description="Toggle a cog by name",
        extras={"category": "Owner", "owner": True}
    )
    @commands.is_owner()
    @app_commands.autocomplete(extension=cog_name_autocomplete)
    @app_commands.describe(extension="Pick a cog to load/unload")
    async def togglecog(self, ctx: commands.Context, extension: str):
        await self._toggle_cog(ctx, extension)

    async def _toggle_cog(self, ctx: commands.Context, ext: str, friendly: str = None):
        if ext in self.bot.extensions:
            await self.bot.unload_extension(ext)
            await ctx.send(f"❌ Unloaded `{friendly or ext}`.")
        else:
            try:
                await self.bot.load_extension(ext)
                await ctx.send(f"✅ Loaded `{friendly or ext}`.")
            except Exception as e:
                await ctx.send(f"⚠️ Failed to load `{friendly or ext}`: {e}")

    @commands.command(name="listcogs", extras={"category": "Toggle", "owner": True})
    @commands.is_owner()
    async def listcogs(self, ctx: commands.Context):
        cog_folder = "cogs"
        all_cogs = [
            f"cogs.{filename[:-3]}"
            for filename in os.listdir(cog_folder)
            if filename.endswith(".py") and filename not in ("__init__.py", "db_utils.py")
        ]

        lines = []
        for ext in sorted(all_cogs):
            status = "✅" if ext in self.bot.extensions else "❌"
            lines.append(f"{status} {ext}")

        embed = discord.Embed(
            title="📦 Cog Status",
            description="\n".join(lines),
            color=discord.Color.blurple()
        )
        await ctx.send(embed=embed)

    @commands.command(name="listappcmds", extras={"category": "Owner", "owner": True})
    @commands.is_owner()
    async def listappcmds(self, ctx):
        cmds = [c.name for c in self.bot.tree.get_commands(guild=discord.Object(id=GUILD_ID))]
        await ctx.send("App commands: " + ", ".join(cmds))

    @commands.command(name="synccmds", extras={"category": "Owner", "owner": True})
    @commands.is_owner()
    async def sync(self, ctx: commands.Context):
        guild = discord.Object(id=GUILD_ID)
        self.bot.tree.copy_global_to(guild=guild)
        cmds = await self.bot.tree.sync(guild=guild)
        sorted_cmds = sorted(cmds, key=lambda c: c.name.lower())
        lines = [f"• {c.name}" for c in sorted_cmds]

        embed = discord.Embed(
            title=f"🔄 Synced {len(sorted_cmds)} commands",
            description="\n".join(lines),
            color=discord.Color.purple()
        )
        await ctx.send(embed=embed)

    # --- staff management ---
    @commands.command(name="puzzlestaffadd", description="Add a user to puzzle staff (owner only)", extras={"category": "Owner", "owner": True})
    @commands.is_owner()
    async def puzzlestaffadd(self, ctx: commands.Context, user: discord.Member):
        staff = set(self.bot.data.get("staff", []))
        staff.add(str(user.id))
        self.bot.data["staff"] = list(staff)
        with open("config.json", "w", encoding="utf-8") as f:
            json.dump(self.bot.data, f, indent=2)
        await ctx.reply(f"{user.mention} added as puzzle staff.", ephemeral=False)

    @commands.command(name="puzzlestaffremove", description="Remove a user from puzzle staff (owner only)", extras={"category": "Owner", "owner": True})
    @commands.is_owner()
    async def puzzlestaffremove(self, ctx: commands.Context, user: discord.User):
        staff = self.bot.data.setdefault("staff", [])
        if str(user.id) not in staff:
            await ctx.reply(f"{user.mention} is not puzzle staff.")
            return
        staff.remove(str(user.id))
        with open("config.json", "w", encoding="utf-8") as f:
            json.dump(self.bot.data, f, indent=2)
        await ctx.reply(f"🗑️ {user.mention} removed from puzzle staff.")

    # --- restart/shutdown/load cogs ---
    @commands.hybrid_command(name="reloadcog", description="Reload a cog by name", extras={"category": "Owner", "owner": True})
    @commands.is_owner()
    @app_commands.autocomplete(extension=cog_name_autocomplete)
    @app_commands.describe(extension="Pick a cog to reload")
    async def reloadcog(self, ctx: commands.Context, extension: str):
        try:
            await self.bot.reload_extension(extension)
            await ctx.reply(f"✅ Reloaded `{extension}` successfully.", ephemeral=False)
        except Exception as e:
            await ctx.reply(f"❌ Failed to reload `{extension}`: {e}", ephemeral=False)

    @commands.command(name="restart", description="Reload all cogs and confirm restart", extras={"category": "Owner", "owner": True})
    @commands.is_owner()
    async def restart(self, ctx: commands.Context):
        await ctx.defer(ephemeral=False)
        reloaded = []
        failed = []

        for ext in list(self.bot.extensions):
            try:
                await self.bot.reload_extension(ext)
                reloaded.append(ext)
            except Exception as e:
                failed.append(f"{ext} ❌ {e}")

        embed = discord.Embed(
            title="✅ Bot Restarted",
            description=f"Reloaded {len(reloaded)} cogs.",
            color=discord.Color.green()
        )
        if failed:
            embed.add_field(name="Errors", value="\n".join(failed[:5]), inline=False)

        await ctx.reply(embed=embed, ephemeral=False)

    @commands.command(name="shutdown", description="Gracefully shut down the bot", extras={"category": "Owner", "owner": True})
    @commands.is_owner()
    async def shutdown(self, ctx: commands.Context):
        await ctx.reply("🛑 Shutting down...", ephemeral=False)
        await asyncio.sleep(0.5)
        await self.bot.close()

    @commands.command(name="status", description="Show bot status and uptime", extras={"category": "Owner", "owner": True})
    @commands.is_owner()
    async def status(self, ctx: commands.Context):
        process = psutil.Process(os.getpid())
        uptime = datetime.datetime.now() - datetime.datetime.fromtimestamp(process.create_time())
        mem = process.memory_info().rss / 1024 / 1024

        embed = discord.Embed(
            title="📊 Bot Status",
            color=discord.Color.blurple()
        )
        embed.add_field(name="Uptime", value=str(uptime).split('.')[0], inline=True)
        embed.add_field(name="Memory", value=f"{mem:.2f} MB", inline=True)
        embed.add_field(name="Python", value=platform.python_version(), inline=True)
        embed.add_field(name="Loaded Cogs", value="\n".join(f"• {ext}" for ext in self.bot.extensions), inline=False)
        embed.add_field(name="Latency", value=f"{self.bot.latency * 1000:.0f} ms", inline=True)

        await ctx.reply(embed=embed, ephemeral=False)

async def setup(bot: commands.Bot):
    await bot.add_cog(OwnerCommandsCog(bot))
