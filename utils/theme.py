import discord

class Emojis:
    SUCCESS = "<:check:1364549836073865247>"
    FAILURE = "<:xxxx:1326424917352255508>"
    LOCK = "<:lockaiw:1328747936204591174>"
    UNLOCK = "<:key_aiw:1328742847456874565>"
    PUZZLE_PIECE = "<:pcaiw:1434756070513053746>"
    TROPHY = "<:Troaiw:1344331648543752203>"
    HOLLOW_PIECE = "<:aiwpiece:1433314933595967630>"
    BURGER = "<:burger_aiw:1329871949685588111>"
    SHINY_DIAMOND = "<:diamondd:1338249348177334333>"
    BADGE_HEART = "<:emoji_169:1338946080783470684>"
    HAMMER = "<:emoji_356:1405949251858731099>"
    EXCLAMATION = "<:exclamation:1342640373356560425>"
    GIFT = "<:gift_aiw:1328764407164960910>"
    HAT = "<:hatter_aiw:1332767352370106421>"
    MEDAL = "<:med1aiw:1344343170137329724>"
    WATCH = "<:pocketwatch_aiw:1332767781229170719>"
    WINNERS_RIBBON = "<:rib2aiw:1344343432184860796>"
    PURPLEROSE_GIF = "<a:PurpleRose:1326415951872397383>"
    ROSE = "<:ro1aiw:1344334809836814366>"
    SWORDS = "<:rumble_aiw:1329872369078108285>"
    DAGGER = "<:emoji_355:1405949198154858779>"
    SHIELD = "<:emoji_353:1405949017476567183>"
    SAVE = "<:save_aiw:1328766382464307312>"
    STAR = "<:staraiw:1395597043820400772>"
    TEACUP = "<:teacup_aiw:1332767169456508998>"
    TEAPOT = "<:teapot_aiw:1332767020403658833>"
    RABBIT_GIF = "<a:whiterabbit_gif:1328740902432276500>"
    ALICE_BOW = "<:balice_bow:1327797527587590217>"
    PURPLE_CELEBRATE = "<a:SNS_Purple:1310676053085261906>"
    DASH_LINE = "<:Linxe:1388347302829363200>"
    PURPLEBUTTERFLY_GIF = "<a:_butterfly_purple:1356505346138701895>"
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
winter_wonderland = Theme(
    color=Colors.CYAN_BLUE,
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
        "theme": "happy_thanksgiving_theme"
    },
    "alice_test_puzzle": {
        "theme": "alice_test_theme"
    },
    # etc...
}