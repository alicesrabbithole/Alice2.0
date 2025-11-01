"""
Centralized configuration for the Alice Bot.
All IDs, paths, and settings should be managed from this file.
"""
from pathlib import Path

# --- Core Bot Settings ---
# Your Discord User ID. This is used to identify the bot owner.
OWNER_ID = 278345224973385728

# --- Guild Settings ---
# The ID of the primary server where the bot operates.
# Used for server-specific commands and features.
WONDERLAND_GUILD_ID = 1309962372269609010

# --- Channel & Role IDs ---
# The channel where the bot will send administrative logs.
LOG_CHANNEL_ID = 1411859714144468992

# The role used by the lock/unlock commands to control channel permissions.
VERIFIED_ROLE_ID = 1309967307149148192

# --- File & Directory Paths ---
# The root directory for all bot data files.
DATA_DIR = Path("data")

# The main database file for storing user progress, settings, etc.
DB_PATH = DATA_DIR / "collected_pieces.json"

# The backup directory for the database.
BACKUP_DIR = DATA_DIR / "backups"

# The root directory where puzzle assets (images, metadata) are stored.
PUZZLES_ROOT = Path("puzzles")

# The file to store role-based permission assignments.
PERMISSIONS_FILE = DATA_DIR / "permissions.json"

# --- Puzzle & UI Settings ---
# A custom emoji to use for puzzle-related messages.
# Example: "<:aiwpiece:1433314933595967630>"
CUSTOM_EMOJI_STRING = "<:aiwpiece:1433314933595967630>"

# A default emoji to use if the custom one is unavailable.
DEFAULT_EMOJI = "🧩"

# The path to the font file for rendering text on images.
# Place a TrueType Font (e.g., "arial.ttf") in your root directory.
FONT_PATH = "arial.ttf"