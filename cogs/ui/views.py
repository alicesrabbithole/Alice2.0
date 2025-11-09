import discord
import logging
import io
from .overlay import render_progress_image
from utils.db_utils import add_piece_to_user, save_data, get_user_pieces
logger = logging.getLogger(__name__)

class PuzzleGalleryView(discord.ui.View):
    def __init__(self, bot, interaction, puzzle_keys: list[str]):
        super().__init__(timeout=300)
        self.bot = bot
        self.interaction = interaction
        self.puzzle_keys = puzzle_keys
        self.index = 0

    async def generate_embed_and_file(self) -> tuple[discord.Embed, discord.File]:
        """
        Generates the embed and image file for the current puzzle.
        """
        # Identify the current puzzle
        puzzle_key = self.puzzle_keys[self.index]
        logger.info(f"[DEBUG] Generating embed for puzzle {puzzle_key} at index {self.index}")

        # Get meta information
        puzzle_meta = self.bot.data["puzzles"][puzzle_key]
        total_pieces = len(self.bot.data.get("pieces", {}).get(puzzle_key, {}))

        # Get user info
        user_id = str(self.interaction.user.id)

        # Canonically get user's collected pieces
        user_pieces = get_user_pieces(self.bot.data, user_id, puzzle_key)

        logger.info(f"[DEBUG] Gallery user_id: {user_id}")
        logger.info(f"[DEBUG] user_pieces for user: {user_pieces}")
        logger.info(f"[DEBUG] puzzle_key: {puzzle_key}")
        logger.info(f"[DEBUG] puzzle_meta: {puzzle_meta}")
        logger.info(f"[DEBUG] total_pieces: {total_pieces}")

        # Render overlay image
        image_bytes = render_progress_image(self.bot.data, puzzle_key, user_pieces)
        logger.info(f"[DEBUG] image_bytes length: {len(image_bytes) if image_bytes else 'None'}")

        # Set up file and embed
        filename = f"{puzzle_key}_progress.png"
        file = discord.File(io.BytesIO(image_bytes), filename=filename)

        embed = discord.Embed(
            title=f"{puzzle_meta['display_name']} Progress",
            description=f"{len(user_pieces)} / {total_pieces} pieces collected",
            color=discord.Color.blurple()
        )
        embed.set_image(url=f"attachment://{filename}")

        # Optionally include collected IDs for debugging/audit:
        if user_pieces:
            embed.add_field(name="Collected IDs", value=", ".join(user_pieces), inline=False)
        else:
            embed.add_field(name="Collected IDs", value="None yet!", inline=False)

        return embed, file

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        logger.info(f"[DEBUG] {interaction.user} clicked Previous, moving to index {self.index - 1}")
        if self.index > 0:
            self.index -= 1
        embed, file = await self.generate_embed_and_file()
        await interaction.response.edit_message(embed=embed, attachments=[file], view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.index < len(self.puzzle_keys) - 1:
            self.index += 1
        embed, file = await self.generate_embed_and_file()
        await interaction.response.edit_message(embed=embed, attachments=[file], view=self)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger)
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.message.delete()
        self.stop()