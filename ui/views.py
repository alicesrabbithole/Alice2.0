import discord
from discord import Interaction
from typing import Optional, List
import logging
import io

import config
from utils.db_utils import add_piece_to_user, save_data, get_puzzle_display_name, get_user_pieces
from .overlay import render_progress_image

logger = logging.getLogger(__name__)


class DropView(discord.ui.View):
    """The view for a puzzle piece drop, containing the 'Collect' button."""

    def __init__(self, bot, puzzle_key: str, puzzle_display_name: str, piece_id: str, claim_limit: int, user_pieces: dict):
        super().__init__(timeout=30.0)
        self.bot = bot
        self.puzzle_key = puzzle_key
        self.puzzle_display_name = puzzle_display_name
        self.piece_id = piece_id
        self.claim_limit = claim_limit
        self.user_pieces = user_pieces  # <--- store actual mapping here!
        self.claimants: List[discord.User] = []
        self.message: Optional[discord.Message] = None

    def _get_partial_emoji(self) -> discord.PartialEmoji:
        """Safely parses the custom emoji string."""
        if config.CUSTOM_EMOJI_STRING:
            try:
                return discord.PartialEmoji.from_str(config.CUSTOM_EMOJI_STRING)
            except (TypeError, ValueError):
                logger.warning(f"Could not parse custom emoji: {config.CUSTOM_EMOJI_STRING}. Falling back to default.")
        return discord.PartialEmoji(name=config.DEFAULT_EMOJI)

    @discord.ui.button(label="Collect", style=discord.ButtonStyle.primary, custom_id="collect_button",
                       emoji=config.CUSTOM_EMOJI_STRING or config.DEFAULT_EMOJI)
    async def collect_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        owned_pieces = self.user_pieces.get(user_id, {}).get(self.puzzle_key, [])

        if self.piece_id in owned_pieces:
            await interaction.response.send_message("You already own this piece!", ephemeral=True)
            return

        if interaction.user in self.claimants:
            await interaction.response.send_message("You already collected this piece during this drop!", ephemeral=True)
            return

        if len(self.claimants) >= self.claim_limit:
            self.remove_item(button)
            await interaction.response.send_message("This drop was already fully claimed!", ephemeral=True)
            if self.message:
                await self.message.edit(view=self)
            await self.post_summary()
            self.stop()
            return

        # Mark the claim
        self.claimants.append(interaction.user)
        await interaction.response.send_message(
            f"You’ve collected piece `{self.piece_id}` of **{self.puzzle_display_name}**!",
            ephemeral=True
        )

        # If claim limit reached, remove button & post summary immediately
        if len(self.claimants) >= self.claim_limit:
            self.remove_item(button)
            if self.message:
                await self.message.edit(view=self)
            await self.post_summary()
            self.stop()

    async def on_timeout(self):
        # Remove the button after timeout
        self.remove_item(self.collect_button)
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.NotFound:
                pass  # Message was deleted, nothing to do
        await self.post_summary()
        self.stop()

    async def post_summary(self):
        """Posts a summary of who collected the piece after the drop ends."""
        if not self.message:
            return
        if not self.claimants:
            summary = f"The drop for the **{self.puzzle_display_name}** puzzle (Piece `{self.piece_id}`) timed out with no collectors."
        else:
            mentions = ', '.join(u.mention for u in self.claimants)
            summary = f"Piece `{self.piece_id}` of the **{self.puzzle_display_name}** puzzle was collected by: {mentions}"
        try:
            await self.message.channel.send(summary, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            pass

class PuzzleGalleryView(discord.ui.View):
    """A paginated view for browsing a user's collected puzzles."""

    def __init__(self, bot, interaction: Interaction, user_puzzle_keys: list[str]):
        super().__init__(timeout=300.0)
        self.bot = bot
        self.interaction = interaction
        self.user_puzzle_keys = user_puzzle_keys
        self.current_index = 0
        self.update_buttons()

    def update_buttons(self):
        """Enable/disable pagination buttons based on the current index."""
        self.first_page.disabled = self.current_index == 0
        self.prev_page.disabled = self.current_index == 0
        self.next_page.disabled = self.current_index >= len(self.user_puzzle_keys) - 1
        self.last_page.disabled = self.current_index >= len(self.user_puzzle_keys) - 1

    async def generate_embed_and_file(self) -> tuple[discord.Embed, Optional[discord.File]]:
        """Generates the embed and image file for the current puzzle."""
        puzzle_key = self.user_puzzle_keys[self.current_index]
        display_name = get_puzzle_display_name(self.bot.data, puzzle_key)
        user_id = self.interaction.user.id
        user_pieces = get_user_pieces(self.bot.data, user_id, puzzle_key)
        total_pieces = len(self.bot.data.get("pieces", {}).get(puzzle_key, {}))

        emoji = config.CUSTOM_EMOJI_STRING or config.DEFAULT_EMOJI
        embed = discord.Embed(
            title=f"{emoji} {display_name}",
            description=f"**Progress:** {len(user_pieces)} / {total_pieces} pieces collected.",
            color=discord.Color.purple()
        ).set_author(name=self.interaction.user.display_name, icon_url=self.interaction.user.display_avatar.url)

        embed.set_footer(text=f"Puzzle {self.current_index + 1} of {len(self.user_puzzle_keys)}")

        filename = f"{puzzle_key}_progress.png"
        logger.info(f"[DEBUG] render_progress_image called for puzzle_key={puzzle_key} with collected_piece_ids={user_pieces}")
        try:
            image_bytes = render_progress_image(self.bot.data, puzzle_key, user_pieces)
            file = discord.File(io.BytesIO(image_bytes), filename=filename)
            embed.set_image(url=f"attachment://{filename}")
        except Exception as e:
            logger.exception(f"Failed to render gallery image for {puzzle_key}")
            embed.add_field(name="⚠️ Render Error", value=f"Could not generate puzzle image: `{e}`")
            file = None

        return embed, file

    async def update_message(self):
        """Updates the original interaction message with the new puzzle view."""
        self.update_buttons()
        embed, file = await self.generate_embed_and_file()
        await self.interaction.edit_original_response(embed=embed, view=self, attachments=[file] if file else [])

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            await self.interaction.edit_original_response(view=self)
        except discord.NotFound:
            pass

    @discord.ui.button(label="<<", style=discord.ButtonStyle.blurple)
    async def first_page(self, interaction: Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.current_index = 0
        await self.update_message()

    @discord.ui.button(label="<", style=discord.ButtonStyle.blurple)
    async def prev_page(self, interaction: Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.current_index -= 1
        await self.update_message()

    @discord.ui.button(label=">", style=discord.ButtonStyle.blurple)
    async def next_page(self, interaction: Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.current_index += 1
        await self.update_message()

    @discord.ui.button(label=">>", style=discord.ButtonStyle.blurple)
    async def last_page(self, interaction: Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.current_index = len(self.user_puzzle_keys) - 1
        await self.update_message()