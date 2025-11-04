import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
import logging

import config
from utils.db_utils import load_data
from utils.log_utils import setup_logging

# --- Setup ---
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

setup_logging()
logger = logging.getLogger(__name__)

# --- Intents and Bot Class ---
intents = discord.Intents.default()
intents.message_content = True  # Make sure this is enabled in the Developer Portal


class AliceBot(commands.Bot):
    def __init__(self):
        super().__init__(

