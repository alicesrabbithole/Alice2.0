# bot.py
import os
import sys
import asyncio
import json
import logging
from typing import Any
from dotenv import load_dotenv
import discord
from discord.ext import commands
from tools.patch_config import patch_config
from cogs.db_utils import load_data, sync_puzzle_images
from cogs.log_utils import log, log_exception

# Logging setup
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(name)s %(message)s")

# path to your config.json (relative to project root)
config_path = os.path.join(os.path.dirname(__file__), "config.json")
# call with project_anchor so patch_config resolves puzzles folder next to bot.py
patch_config(config_path, project_anchor=__file__)

# Load environment
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise SystemExit("❌ DISCORD_TOKEN not set in .env")

GUILD_ID = 1309962372269609010

# Bot setup
intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
intents.members = True
intents.message_content = True

bot: commands.Bot = commands.Bot(command_prefix="!", intents=intents)

# Inject shared state before loading cogs
bot.data: dict[str, Any] = json.load(open("config.json", "r", encoding="utf-8"))
bot.collected: dict[str, Any] = load_data()

_extensions_loaded = False
_synced_tree = False

@bot.event
async def on_ready():
    global _synced_tree
    print(f"✅ Logged in as {bot.user} (id={bot.user.id})")
    print("📌 Prefix commands:", [c.name for c in bot.commands])
    print("📦 Loaded extensions:", list(bot.extensions.keys()))

    # Refresh in-memory collected data and sync puzzles to bot.data
    bot.collected = load_data()
    bot.data = {"puzzles": {}, "pieces": {}}
    sync_puzzle_images(bot)

    if _synced_tree:
        return
    _synced_tree = True

    try:
        bot.tree.copy_global_to(guild=discord.Object(id=GUILD_ID))
        synced = await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        print("🌐 Synced guild commands:", [c.name for c in synced])
    except Exception as e:
        print("❌ Failed to sync command tree:", e)

async def load_all_cogs():
    global _extensions_loaded
    if _extensions_loaded:
        return
    _extensions_loaded = True

    cog_folder = "cogs"
    if not os.path.isdir(cog_folder):
        print("❌ Cog folder not found:", cog_folder)
        return

    # ✅ Centralized exclusion list for helper modules
    excluded = {
        "__init__.py",
        "db_utils.py",
        "preview_cache.py",
        "log_utils.py",
        "puzzle_composer.py",
        "patch_config.py",
        "constants.py"
    }

    for filename in os.listdir(cog_folder):
        if filename.endswith(".py") and filename not in excluded:
            cog_name = f"{cog_folder}.{filename[:-3]}"
            try:
                await bot.load_extension(cog_name)
                print(f"✅ Loaded cog: {cog_name}")
            except Exception as e:
                print(f"⚠️ Failed to load {cog_name}: {e}")

async def main():
    await load_all_cogs()
    print("🚀 Cogs loaded; starting bot.")
    try:
        await bot.start(TOKEN)
    except KeyboardInterrupt:
        print("🛑 KeyboardInterrupt received; shutting down.")
        await bot.close()
    except Exception as e:
        print("💥 Error while running bot:", e, file=sys.stderr)
        await bot.close()
        raise

@bot.event
async def on_command(ctx):
    await log(bot, f"📥 `{ctx.command}` used by {ctx.author} in {ctx.channel.mention}")

@bot.event
async def on_command_error(ctx, error):
    await log_exception(bot, f"command `{ctx.command}` by {ctx.author}", error)

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    await log_exception(bot, f"slash command `{interaction.command}` by {interaction.user}", error)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        print("🔥 Fatal error:", exc, file=sys.stderr)
        sys.exit(1)
