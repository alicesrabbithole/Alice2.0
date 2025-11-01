"""
This file contains all the visual theme elements for the bot,
including custom emojis and embed colors.
"""
import discord

class Emojis:
    """A class to hold all custom emoji strings for easy access."""
    # --- General Emojis ---
    SUCCESS = "<:success:123456789012345678>"
    FAILURE = "<:failure:123456789012345678>"
    # ... etc

    # --- Moderation Emojis ---
    LOCK = "<:lockaiw:1328747936204591174>"       # Your actual lock emoji
    UNLOCK = "<:key_aiw:1328742847456874565>"   # Your actual unlock emoji

    # ... other emoji categories

class Colors:
    """A class to hold all custom embed colors."""
    PRIMARY = discord.Color(0x793aab)
    SUCCESS = discord.Color(0x57F287)
    FAILURE = discord.Color(0xED4245)      # A bright red for errors
    # ... etc