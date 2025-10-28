print("🧠 Running bot.py from:", __file__)

# bot.py

import os
import sys
import asyncio
import logging
from typing import Any

import discord
from discord.ext import commands
from dotenv import load_dotenv
from cogs.db_utils import load_data, save_data, sync_puzzle_images, slugify_key
from cogs.log_utils import log, log_exception
from tools.patch_config import patch_config
from cogs.db_utils import normalize_all_puzzle_keys
from tools.puzzle_sync import initialize_puzzle_data

# Logging setup
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


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
bot.collected = load_data()

# Load config and normalize puzzle keys
bot.data = load_data()
bot.data.setdefault("render_flags", {})


def normalize_bot_data(bot):
    bot.data["puzzles"] = {
        slugify_key(k): v for k, v in bot.data.get("puzzles", {}).items()
    }
    bot.data["pieces"] = {
        slugify_key(k): v for k, v in bot.data.get("pieces", {}).items()
    }

normalize_bot_data(bot)

logger.info("✅ Normalized bot.data['pieces'] keys: %s", list(bot.data["pieces"].keys()))
logger.info("✅ Sample key check: 'alice_test' in pieces → %s", 'alice_test' in bot.data["pieces"])

_extensions_loaded = False
_synced_tree = False

@bot.event
async def on_ready():
    global _synced_tree
    initialize_puzzle_data(bot)

    print(f"✅ Logged in as {bot.user} (id={bot.user.id})")
    print("📌 Prefix commands:", [c.name for c in bot.commands])
    print("📦 Loaded extensions:", list(bot.extensions.keys()))
    print("🔍 Available puzzle slugs:", list(bot.data["puzzles"].keys()))

    if not _synced_tree:
        _synced_tree = True
        try:
            bot.tree.copy_global_to(guild=discord.Object(id=GUILD_ID))
            synced = await bot.tree.sync(guild=discord.Object(id=GUILD_ID))
            print("🌐 Synced guild commands:", [c.name for c in synced])
        except Exception as e:
            print("❌ Failed to sync command tree:", e)

    if not _synced_tree:
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

    excluded = {
        "__init__.py",
        "db_utils.py",
        "preview_cache.py",
        "log_utils.py",
        "puzzle_composer.py",
        "patch_config.py",
        "constants.py",
        "drop_config.py"
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
