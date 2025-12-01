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

class Theme:
    def __init__(self, color, button_color, emoji):
        self.color = color
        self.button_color = button_color
        self.emoji = emoji

happy_thanksgiving_theme = Theme(
    color=Colors.CYAN_BLUE,
    button_color=Colors.NEON_PURPLE,
    emoji=Emojis.PUZZLE_PIECE
)
alice_test_theme = Theme(
    color=Colors.NEON_PURPLE,
    button_color=Colors.PRIMARY,
    emoji=Emojis.TROPHY
)
THEMES = {
    "happy_thanksgiving_theme": happy_thanksgiving_theme,
    "alice_test_theme": alice_test_theme,
    # Add more themes as needed
}