"""
This file contains all the visual theme elements for the bot,
including custom emojis and embed colors.
"""
import discord

# --- Color Palette ---
# Define your two theme colors here.
CYAN_BLUE = 0x00FFFF
NEON_PURPLE = 0x9D00FF # A vibrant, neon-like purple

# --- Active Theme ---
# To change the theme for the entire bot, just change this one line!
# Options: CYAN_BLUE or NEON_PURPLE
THEME_COLOR = CYAN_BLUE

class Emojis:
    """A class to hold all custom emoji strings for easy access."""
    # --- General Emojis ---
    SUCCESS = "<:check:1364549836073865247>"
    FAILURE = "<:xxxx:1326424917352255508>"
    # ... etc

    # --- Moderation Emojis ---
    LOCK = "<:lockaiw:1328747936204591174>"       # Your actual lock emoji
    UNLOCK = "<:key_aiw:1328742847456874565>"   # Your actual unlock emoji
    PUZZLE_PIECE = "<:pcaiw:1434756070513053746>"
    TROPHY = "<:Troaiw:1344331648543752203>"

    # ... other emoji categories

class Colors:
    """A class to hold all custom embed colors."""
    PRIMARY = discord.Color(0x793aab)
    SUCCESS = discord.Color(0x00827F)
    FAILURE = discord.Color(0x850101)      # A bright red for errors
    # ... etc