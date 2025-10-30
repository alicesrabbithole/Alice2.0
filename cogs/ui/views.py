import discord
from discord import Interaction
from typing import Optional, List
import logging
import io

from ..utils.db_utils import add_piece_to_user, save_data, get_puzzle_display_name
from .overlay import render_progress_image

logger = logging.getLogger(__name__)

CUSTOM_EMOJI_STRING = "<:aiwpiece:1433314933595967630>"
DEFAULT_EMOJI = "🧩"


# --- View for Puzzle Piece Drops ---
class DropView(discord.ui.View):
    def __init__(self, bot, puzzle_key: str, puzzle_display_name: str, piece_id: str, claim_limit: int):
        super().__init__(timeout=300.0)
        self.bot, self.puzzle_key, self.puzzle_display_name, self.piece_id, self.claim_limit = bot, puzzle_key, puzzle_display_name, piece_id, claim_limit
        self.claimants: List[discord.User] = []
        self.message: Optional[discord.Message] = None
        self.collect_button: discord.ui.Button = self.children[0]
        self.set_button_emoji()

    def set_button_emoji(self):
        final_emoji = DEFAULT_EMOJI
        if CUSTOM_EMOJI_STRING:
            try:
                final_emoji = discord.PartialEmoji.from_str(CUSTOM_EMOJI_STRING) or DEFAULT_EMOJI
            except:
                logger.warning(f"Could not parse custom emoji: {CUSTOM_EMOJI_STRING}.")
        self.collect_button.emoji = final_emoji

    async def on_timeout(self):
        self.collect_button.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except:
                pass
        await self.post_summary()

    async def post_summary(self):
        if not self.message: return
        if not self.claimants:
            summary = f"The drop for the **{self.puzzle_display_name}** puzzle (Piece `{self.piece_id}`) timed out with no collectors."
        else:
            summary = f"Piece `{self.piece_id}` of the **{self.puzzle_display_name}** puzzle was collected by: {', '.join(u.mention for u in self.claimants)}"
        try:
            await self.message.channel.send(summary)
        except:
            pass

    @discord.ui.button(label="Collect Piece", style=discord.ButtonStyle.secondary)
    async def collect_button(self, interaction: Interaction, button: discord.ui.Button):
        if not add_piece_to_user(self.bot.data, interaction.user.id, self.puzzle_key, self.piece_id):
            await interaction.response.send_message("You already have this piece!", ephemeral=True);
            return

        save_data(self.bot.data)
        self.claimants.append(interaction.user)
        await interaction.response.send_message(
            f"✅ You collected Piece `{self.piece_id}` for the **{self.puzzle_display_name}** puzzle!", ephemeral=True)

        if len(self.claimants) >= self.claim_limit:
            button.disabled = True;
            button.label = "Drop Fully Claimed"
            if self.message: await self.message.edit(view=self)
            await self.post_summary();
            self.stop()


# --- View for Browsing Puzzles (The New Gallery) ---
class PuzzleGalleryView(discord.ui.View):
    def __init__(self, bot, interaction: Interaction, user_puzzle_keys: list[str]):
        super().__init__(timeout=180.0)
        self.bot = bot
        self.interaction = interaction
        self.user_puzzle_keys = user_puzzle_keys
        self.current_index = 0
        self.update_buttons()

    def update_buttons(self):
        """Enable/disable buttons based on the current index."""
        self.children[0].disabled = self.current_index == 0
        self.children[1].disabled = self.current_index == 0
        self.children[2].disabled = self.current_index == len(self.user_puzzle_keys) - 1
        self.children[3].disabled = self.current_index == len(self.user_puzzle_keys) - 1

    async def generate_embed_and_file(self) -> tuple[discord.Embed, discord.File, str]:
        """Generates the embed and file for the current puzzle."""
        puzzle_key = self.user_puzzle_keys[self.current_index]
        display_name = get_puzzle_display_name(self.bot.data, puzzle_key)
        user_pieces = self.bot.data.get("user_pieces", {}).get(str(self.interaction.user.id), {}).get(puzzle_key, [])
        total_pieces = len(self.bot.data.get("pieces", {}).get(puzzle_key, {}))

        emoji = CUSTOM_EMOJI_STRING or DEFAULT_EMOJI
        embed = discord.Embed(
            title=f"{emoji} {display_name}",
            description=f"**Progress:** {len(user_pieces)} / {total_pieces} pieces collected.",
            color=discord.Color.purple()
        ).set_author(name=self.interaction.user.display_name, icon_url=self.interaction.user.display_avatar.url)

        embed.set_footer(text=f"Puzzle {self.current_index + 1} of {len(self.user_puzzle_keys)}")

        filename = f"{puzzle_key}_progress.png"
        image_bytes = render_progress_image(self.bot.data, puzzle_key, user_pieces)
        file = discord.File(io.BytesIO(image_bytes), filename=filename)
        embed.set_image(url=f"attachment://{filename}")

        return embed, file, filename

    async def update_message(self):
        """Updates the original message with the new puzzle view."""
        self.update_buttons()
        embed, file, filename = await self.generate_embed_and_file()
        # We need to clear existing attachments and add the new one
        await self.interaction.edit_original_response(embed=embed, view=self, attachments=[file])

    @discord.ui.button(label="<<", style=discord.ButtonStyle.primary)
    async def first_page(self, interaction: Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.current_index = 0
        await self.update_message()

    @discord.ui.button(label="<", style=discord.ButtonStyle.primary)
    async def prev_page(self, interaction: Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.current_index -= 1
        await self.update_message()

    @discord.ui.button(label=">", style=discord.ButtonStyle.primary)
    async def next_page(self, interaction: Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.current_index += 1
        await self.update_message()

    @discord.ui.button(label=">>", style=discord.ButtonStyle.primary)
    async def last_page(self, interaction: Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.current_index = len(self.user_puzzle_keys) - 1
        await self.update_message()