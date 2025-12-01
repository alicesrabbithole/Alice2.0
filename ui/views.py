import discord
from discord import Interaction
from typing import Optional, List
import logging
import io

import config
from utils.db_utils import (
    add_piece_to_user,
    save_data,
    get_puzzle_display_name,
    get_user_pieces,
)
from .overlay import render_progress_image

# Per-puzzle theme system — adjust imports as needed
from utils.theme import Emojis, Colors, THEMES, PUZZLE_CONFIG

logger = logging.getLogger(__name__)

class DropView(discord.ui.View):
    """The view for a puzzle piece drop, containing the 'Collect' button."""

    def __init__(self, bot, puzzle_key: str, puzzle_display_name: str, piece_id: str, claim_limit: int, button_color=None):
        super().__init__(timeout=30.0)
        self.bot = bot
        self.puzzle_key = puzzle_key
        self.puzzle_display_name = puzzle_display_name
        self.piece_id = piece_id
        self.claim_limit = claim_limit
        self.claimants: List[discord.User] = []
        self.message: Optional[discord.Message] = None
        self.summary_sent = False # Only allows posting once

        # Set emoji for the button
        self.collect_button.emoji = self._get_partial_emoji()

    def _get_partial_emoji(self) -> discord.PartialEmoji:
        """Safely parses the custom emoji string."""
        if config.CUSTOM_EMOJI_STRING:
            try:
                return discord.PartialEmoji.from_str(config.CUSTOM_EMOJI_STRING)
            except (TypeError, ValueError):
                logger.warning(f"Could not parse custom emoji: {config.CUSTOM_EMOJI_STRING}. Falling back to default.")
        return discord.PartialEmoji(name=config.DEFAULT_EMOJI)

    async def on_timeout(self):
        self.remove_item(self.collect_button)
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.NotFound:
                pass
            await self.post_summary()
            self.stop()

    @discord.ui.button(label="Collect Piece", style=discord.ButtonStyle.primary)
    async def collect_button(self, interaction: Interaction, button: discord.ui.Button):
        if not add_piece_to_user(self.bot.data, interaction.user.id, self.puzzle_key, self.piece_id):
            return await interaction.response.send_message("You already have this piece!", ephemeral=True)

        save_data(self.bot.data)
        self.claimants.append(interaction.user)
        await interaction.response.send_message(
            f"✅ You collected Piece `{self.piece_id}` for the **{self.puzzle_display_name}** puzzle!", ephemeral=True
        )

        # ==== Completion Role, Finisher, and Logging ====
        meta = PUZZLE_CONFIG.get(self.puzzle_key, {})
        role_id = meta.get("completion_role_id")
        user_id = interaction.user.id
        user_pieces = get_user_pieces(self.bot.data, user_id, self.puzzle_key)
        total_pieces = len(self.bot.data.get("pieces", {}).get(self.puzzle_key, {}))

        if len(user_pieces) == total_pieces:
            # Award completion role
            if role_id and interaction.guild:
                role = interaction.guild.get_role(role_id)
                if role and role not in interaction.user.roles:
                    await interaction.user.add_roles(role,
                                                     reason=f"Completed puzzle: {meta.get('display_name', self.puzzle_key)}")
                    await interaction.followup.send(
                        f"🏆 Congratulations! You completed **{meta.get('display_name', self.puzzle_key)}** and earned the {role.mention} role!",
                        ephemeral=True
                    )
                    # Log to Discord channel
                    log_channel_id = 1411859714144468992
                    log_channel = interaction.guild.get_channel(log_channel_id)
                    if not log_channel:
                        try:
                            log_channel = await interaction.guild.fetch_channel(log_channel_id)
                        except Exception:
                            log_channel = None
                    if log_channel:
                        await log_channel.send(
                            f"📝 {interaction.user.mention} completed **{meta.get('display_name', self.puzzle_key)}** and was awarded {role.mention}."
                        )
            # Track first finisher
            finishers = self.bot.data.setdefault("puzzle_finishers", {}).setdefault(self.puzzle_key, [])
            if user_id not in [f["user_id"] for f in finishers]:
                import datetime
                finishers.append({"user_id": user_id, "timestamp": datetime.datetime.utcnow().isoformat()})
                save_data(self.bot.data)
        # ==== End Completion Role, Finisher, and Logging ====

        if len(self.claimants) >= self.claim_limit:
            self.remove_item(button)
            if self.message:
                await self.message.edit(view=self)
            await self.post_summary()
            self.stop()

    async def post_summary(self):
        if self.summary_sent:
            return
        self.summary_sent = True

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

    def __init__(self, bot, interaction: Interaction, user_puzzle_keys: list[str], current_index=0):
        super().__init__(timeout=300.0)
        self.bot = bot
        self.interaction = interaction
        self.user_puzzle_keys = user_puzzle_keys
        self.current_index = current_index
        self.update_buttons()

    def update_buttons(self):
        """Enable/disable pagination buttons based on the current index."""
        self.first_page.disabled = self.current_index == 0
        self.prev_page.disabled = self.current_index == 0
        self.next_page.disabled = self.current_index >= len(self.user_puzzle_keys) - 1
        self.last_page.disabled = self.current_index >= len(self.user_puzzle_keys) - 1

    async def generate_embed_and_file(self) -> tuple[discord.Embed, Optional[discord.File]]:
        puzzle_key = self.user_puzzle_keys[self.current_index]
        meta = PUZZLE_CONFIG.get(puzzle_key, {})
        theme_name = meta.get("theme")
        theme = THEMES.get(theme_name) if theme_name else None

        display_name = meta.get("display_name", get_puzzle_display_name(self.bot.data, puzzle_key))
        user_id = self.interaction.user.id
        user_pieces = get_user_pieces(self.bot.data, user_id, puzzle_key)
        total_pieces = len(self.bot.data.get("pieces", {}).get(puzzle_key, {}))

        emoji = theme.emoji if theme else (
            config.CUSTOM_EMOJI_STRING if hasattr(config, "CUSTOM_EMOJI_STRING") else Emojis.PUZZLE_PIECE)
        embed_color = theme.color if theme else Colors.THEME_COLOR

        desc = f"**Progress:** {len(user_pieces)} / {total_pieces} pieces collected."

        # Add first finisher info
        finishers = self.bot.data.get("puzzle_finishers", {}).get(puzzle_key, [])
        if finishers:
            first = finishers[0]
            first_user = self.bot.get_user(first["user_id"]) or await self.bot.fetch_user(first["user_id"])
            desc += f"\n**First Finisher:** {first_user.mention} ({first['timestamp']})"

        # Add completion role info
        role_id = meta.get("completion_role_id")
        role_text = ""
        if role_id and self.interaction.guild:
            role = self.interaction.guild.get_role(role_id)
            if role:
                role_text = f"\n**Completion Role:** {role.mention}"
        elif role_id:
            role_text = f"\n**Completion Role:** <@&{role_id}>"

        desc += role_text

        embed = discord.Embed(
            title=f"{emoji} {display_name}",
            description=desc,
            color=embed_color
        )
        embed.set_author(name=self.interaction.user.display_name, icon_url=self.interaction.user.display_avatar.url)
        embed.set_footer(text=f"Puzzle {self.current_index + 1} of {len(self.user_puzzle_keys)}")

        filename = f"{puzzle_key}_progress.png"
        logger.info(
            f"[DEBUG] render_progress_image called for puzzle_key={puzzle_key} with collected_piece_ids={user_pieces}")
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

    # PAGINATION BUTTONS
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

    # --- LEADERBOARD BUTTON ---
    @discord.ui.button(label="Leaderboard", style=discord.ButtonStyle.primary)
    async def goto_leaderboard(self, interaction: Interaction, button: discord.ui.Button):
        view = LeaderboardView(
            self.bot,
            interaction,
            self.user_puzzle_keys,
            current_page=self.current_index,
        )
        embed, file = await view.generate_leaderboard_embed()
        await interaction.response.edit_message(embed=embed, view=view, attachments=[file] if file else [])


class LeaderboardView(discord.ui.View):
    """A paginated view for browsing leaderboards across puzzles."""

    def __init__(self, bot, interaction: Interaction, puzzle_keys: List[str], current_page=0):
        super().__init__(timeout=300.0)
        self.bot = bot
        self.interaction = interaction
        self.puzzle_keys = puzzle_keys
        self.page = current_page
        self.update_buttons()

    def update_buttons(self):
        self.first_page.disabled = self.page == 0
        self.prev_page.disabled = self.page == 0
        self.next_page.disabled = self.page >= len(self.puzzle_keys) - 1
        self.last_page.disabled = self.page >= len(self.puzzle_keys) - 1

    async def generate_leaderboard_embed(self) -> tuple[discord.Embed, None]:
        puzzle_key = self.puzzle_keys[self.page]
        meta = PUZZLE_CONFIG.get(puzzle_key, {})
        theme_name = meta.get("theme")
        theme = THEMES.get(theme_name) if theme_name else None

        display_name = meta.get("display_name", get_puzzle_display_name(self.bot.data, puzzle_key))
        emoji = theme.emoji if theme else Emojis.TROPHY
        embed_color = theme.color if theme else Colors.THEME_COLOR

        all_user_pieces = self.bot.data.get("user_pieces", {})
        leaderboard_data = [
            (int(user_id), len(user_puzzles.get(puzzle_key, [])))
            for user_id, user_puzzles in all_user_pieces.items()
            if puzzle_key in user_puzzles and len(user_puzzles[puzzle_key]) > 0
        ]
        leaderboard_data.sort(key=lambda x: (-x[1], x[0]))

        desc_lines = []
        if leaderboard_data:
            for i, (user_id, count) in enumerate(leaderboard_data[:20], start=1):
                user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
                user_mention = user.mention if user else f"User (`{user_id}`)"
                desc_lines.append(f"**{i}.** {user_mention} - `{count}` pieces")
        else:
            desc_lines.append("No one has collected any pieces for this puzzle yet.")

        # Add first finisher info
        finishers = self.bot.data.get("puzzle_finishers", {}).get(puzzle_key, [])
        if finishers:
            first = finishers[0]
            first_user = self.bot.get_user(first["user_id"]) or await self.bot.fetch_user(first["user_id"])
            desc_lines.append(f"\n**First Finisher:** {first_user.mention} ({first['timestamp']})")

        # Add completion role info
        role_id = meta.get("completion_role_id")
        if role_id and self.interaction.guild:
            role = self.interaction.guild.get_role(role_id)
            if role:
                desc_lines.append(f"**Completion Role:** {role.mention}")
        elif role_id:
            desc_lines.append(f"**Completion Role:** <@&{role_id}>")

        embed = discord.Embed(
            title=f"{emoji} Leaderboard for {display_name}",
            description="\n".join(desc_lines),
            color=embed_color,
        )
        embed.set_footer(text=f"Puzzle {self.page + 1} of {len(self.puzzle_keys)}")
        return embed, None

    async def update_message(self):
        self.update_buttons()
        embed, file = await self.generate_leaderboard_embed()
        await self.interaction.edit_original_response(embed=embed, view=self, attachments=[file] if file else [])

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            await self.interaction.edit_original_response(view=self)
        except discord.NotFound:
            pass

    # PAGINATION BUTTONS
    @discord.ui.button(label="<<", style=discord.ButtonStyle.blurple)
    async def first_page(self, interaction: Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.page = 0
        await self.update_message()

    @discord.ui.button(label="<", style=discord.ButtonStyle.blurple)
    async def prev_page(self, interaction: Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.page -= 1
        await self.update_message()

    @discord.ui.button(label=">", style=discord.ButtonStyle.blurple)
    async def next_page(self, interaction: Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.page += 1
        await self.update_message()

    @discord.ui.button(label=">>", style=discord.ButtonStyle.blurple)
    async def last_page(self, interaction: Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.page = len(self.puzzle_keys) - 1
        await self.update_message()

    # --- GALLERY BUTTON ---
    @discord.ui.button(label="Gallery", style=discord.ButtonStyle.secondary)
    async def goto_gallery(self, interaction: Interaction, button: discord.ui.Button):
        view = PuzzleGalleryView(
            self.bot,
            interaction,
            self.puzzle_keys,
            current_index=self.page,
        )
        embed, file = await view.generate_embed_and_file()
        await interaction.response.edit_message(embed=embed, view=view, attachments=[file] if file else [])