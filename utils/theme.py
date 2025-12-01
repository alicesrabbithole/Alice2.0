import discord

class Emojis:
    SUCCESS = "<:check:1364549836073865247>"
    FAILURE = "<:xxxx:1326424917352255508>"
    LOCK = "<:lockaiw:1328747936204591174>"
    UNLOCK = "<:key_aiw:1328742847456874565>"
    PUZZLE_PIECE = "<:pcaiw:1434756070513053746>"
    TROPHY = "<:Troaiw:1344331648543752203>"
    # ... etc for your custom emojis ...

class Colors:
    PRIMARY = discord.Color(0x793aab)
    SUCCESS = discord.Color(0x00827F)
    FAILURE = discord.Color(0x850101)
    CYAN_BLUE = 0x00FFFF
    NEON_PURPLE = 0x9D00FF
    THEME_COLOR = CYAN_BLUE
    ORANGE = 0xFF5C00

    # # PUZZLE THEMES BELOW - - - - - - - - - - - - - - - - - - - - - - -

class Theme:
    def __init__(self, color, button_color, emoji):
        self.color = color
        self.button_color = button_color
        self.emoji = emoji


happy_thanksgiving_theme = Theme(
    color=Colors.ORANGE,
    button_color=discord.ButtonStyle.secondary,
    emoji=Emojis.PUZZLE_PIECE
)
alice_test_theme = Theme(
    color=Colors.NEON_PURPLE,
    button_color=discord.ButtonStyle.primary,
    emoji=Emojis.PUZZLE_PIECE
)
THEMES = {
    "happy_thanksgiving_theme": happy_thanksgiving_theme,
    "alice_test_theme": alice_test_theme,
    # Add more themes as needed
}

PUZZLE_CONFIG = {
    "thanksgiving_puzzle": {
        "theme": "happy_thanksgiving_theme",
        "completion_role_id": 1443655705461653534,
    },
    "alice_test_puzzle": {
        "theme": "alice_test_theme",
        "completion_role_id": 1379974318213173360,
    },
    # etc...
}