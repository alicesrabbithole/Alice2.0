import discord
import logging

from utils.db_utils import add_piece_to_user, save_data

logger = logging.getLogger(__name__)


class DropView(discord.ui.View):
    """A view for a puzzle piece drop, containing the 'Collect' button."""

    def __init__(self, bot, puzzle_key: str, puzzle_display_name: str, piece_id: str, claim_limit: int):
        super().__init__(timeout=300)  # 5-minute timeout
        self.bot = bot
        self.puzzle_key = puzzle_key
        self.puzzle_display_name = puzzle_display_name
        self.piece_id = piece_id
        self.claim_limit = claim_limit
        self.claimants = []
        self.message = None

    async def on_timeout(self):
        """Called when the view times out. Now only triggers the summary."""
        # When timing out, we use the stored self.message
        await self.post_summary(message_to_edit=self.message)
        self.stop()

    async def post_summary(self, message_to_edit: discord.Message):
        """Posts a summary and ensures the original message's buttons are removed."""
        # --- THIS IS THE ROBUST FIX ---
        # First, remove the button from the message that triggered the event.
        if message_to_edit:
            try:
                await message_to_edit.edit(view=None)
            except (discord.NotFound, discord.HTTPException):
                pass  # Ignore if message is gone.

        # If no one claimed the piece, do nothing further.
        if not self.claimants:
            return

        # If there were claimants, post the summary message.
        mentions = ', '.join(u.mention for u in self.claimants)
        summary = f"Piece `{self.piece_id}` of the **{self.puzzle_display_name}** puzzle was collected by: {mentions}"
        try:
            # Use the channel from the edited message to send the summary.
            await message_to_edit.channel.send(summary, allowed_mentions=discord.AllowedMentions.none())
        except (AttributeError, discord.HTTPException):
            pass # Ignore if channel is not found or other send errors occur.

    @discord.ui.button(label="Collect", style=discord.ButtonStyle.primary, emoji="✨")
    async def collect_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Callback for the 'Collect' button."""
        if interaction.user in self.claimants:
            await interaction.response.send_message("You have already collected this piece!", ephemeral=True)
            return

        if len(self.claimants) >= self.claim_limit:
            await interaction.response.send_message("This puzzle piece has already been fully claimed!", ephemeral=True)
            return

        if add_piece_to_user(self.bot.data, interaction.user.id, self.puzzle_key, self.piece_id):
            self.claimants.append(interaction.user)
            save_data(self.bot.data)
            await interaction.response.send_message(
                f"You collected Piece `{self.piece_id}` of the **{self.puzzle_display_name}** puzzle!",
                ephemeral=True
            )
        else:
            await interaction.response.send_message("You already have this piece in your collection.", ephemeral=True)
            if interaction.user not in self.claimants:
                self.claimants.append(interaction.user)

        # Check if the claim limit has been reached
        if len(self.claimants) >= self.claim_limit:
            self.stop()
            # --- THIS IS THE FIX ---
            # Pass the message from the interaction directly. This is 100% reliable.
            await self.post_summary(message_to_edit=interaction.message)
