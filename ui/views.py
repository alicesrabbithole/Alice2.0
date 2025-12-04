import io
import logging
from typing import Optional, List, Tuple

import discord
from discord import Interaction
from PIL import Image as PILImage

import config
from utils.db_utils import get_puzzle_display_name, get_user_pieces
from .overlay import render_progress_image
from utils.theme import Emojis, Colors, THEMES, PUZZLE_CONFIG

logger = logging.getLogger(__name__)

# -------------------------
# PuzzleGalleryView
# -------------------------
class PuzzleGalleryView(discord.ui.View):
    """A paginated view for browsing a user's collected puzzles."""

    # Pixels to crop from the bottom of the generated image to remove the progress bar.
    # Set to 0 to disable cropping.
    PROGRESS_BAR_CROP_HEIGHT = 28

    def __init__(
        self,
        bot,
        interaction: Optional[Interaction],
        user_puzzle_keys: list[str],
        current_index: int = 0,
        owner_id: Optional[int] = None,
    ):
        super().__init__(timeout=300.0)
        self.bot = bot
        # The Interaction provided when the view was created (may be None for prefix sends)
        self.interaction = interaction
        self.user_puzzle_keys = user_puzzle_keys
        self.current_index = current_index

        # The owner of the gallery (whose pieces are shown) is captured at creation time.
        # Defaults to the creating interaction user if provided else None.
        self.owner_id = owner_id or (interaction.user.id if interaction and interaction.user else None)

        # The opener (viewer) who initially invoked the gallery; kept if you want to restrict controls.
        self.opener_id = interaction.user.id if interaction and interaction.user else None

        self.update_buttons()

    def update_buttons(self):
        """Enable/disable pagination buttons based on the current index."""
        self.first_page.disabled = self.current_index == 0
        self.prev_page.disabled = self.current_index == 0
        self.next_page.disabled = self.current_index >= len(self.user_puzzle_keys) - 1
        self.last_page.disabled = self.current_index >= len(self.user_puzzle_keys) - 1

    async def generate_embed_and_file(self) -> Tuple[discord.Embed, Optional[discord.File]]:
        puzzle_key = self.user_puzzle_keys[self.current_index]
        meta = PUZZLE_CONFIG.get(puzzle_key, {})
        theme_name = meta.get("theme")
        theme = THEMES.get(theme_name) if theme_name else None

        display_name = meta.get("display_name", get_puzzle_display_name(self.bot.data, puzzle_key))

        owner_id = self.owner_id
        owner_user = None
        if owner_id:
            owner_user = self.bot.get_user(owner_id) or await self.bot.fetch_user(owner_id)

        # Use the owner_id to look up their pieces for this puzzle.
        user_pieces = get_user_pieces(self.bot.data, owner_id, puzzle_key) if owner_id else []
        total_pieces = len(self.bot.data.get("pieces", {}).get(puzzle_key, {}))

        emoji = theme.emoji if theme else (
            config.CUSTOM_EMOJI_STRING if hasattr(config, "CUSTOM_EMOJI_STRING") else Emojis.PUZZLE_PIECE
        )
        embed_color = theme.color if theme else Colors.THEME_COLOR

        desc = f"**Progress:** {len(user_pieces)} / {total_pieces} pieces collected."

        # Add first finisher info
        finishers = self.bot.data.get("puzzle_finishers", {}).get(puzzle_key, [])
        if finishers:
            first = finishers[0]
            first_user = self.bot.get_user(first["user_id"]) or await self.bot.fetch_user(first["user_id"])
            desc += f"\n**First Finisher:** {first_user.mention} ({first['timestamp']})"

        # Add reward role info
        role_id = meta.get("reward_role_id")
        role_text = ""
        if role_id and self.interaction and self.interaction.guild:
            role = self.interaction.guild.get_role(int(role_id))
            if role:
                role_text = f"\n**Reward Role:** {role.mention}"
        elif role_id:
            role_text = f"\n**Reward Role:** <@&{role_id}>"

        desc += role_text

        embed = discord.Embed(title=f"{emoji} {display_name}", description=desc, color=embed_color)

        # Author should reflect the owner of the gallery, not the clicker.
        if owner_user:
            embed.set_author(name=owner_user.display_name, icon_url=owner_user.display_avatar.url)
        else:
            # Fallback to the creating interaction user or a generic label if none
            if self.interaction and self.interaction.user:
                embed.set_author(name=self.interaction.user.display_name, icon_url=self.interaction.user.display_avatar.url)
            else:
                embed.set_author(name=display_name)

        embed.set_footer(text=f"Puzzle {self.current_index + 1} of {len(self.user_puzzle_keys)}")

        filename = f"{puzzle_key}_progress.png"
        logger.info(
            "[DEBUG] render_progress_image called for puzzle_key=%s with collected_piece_ids=%s (owner=%s)",
            puzzle_key,
            user_pieces,
            owner_id,
        )

        try:
            image_bytes = render_progress_image(self.bot.data, puzzle_key, user_pieces)

            # Crop the bottom progress bar if present.
            if image_bytes and self.PROGRESS_BAR_CROP_HEIGHT > 0:
                with PILImage.open(io.BytesIO(image_bytes)) as img:
                    if img.height > self.PROGRESS_BAR_CROP_HEIGHT:
                        cropped = img.crop((0, 0, img.width, img.height - self.PROGRESS_BAR_CROP_HEIGHT))
                    else:
                        cropped = img.copy()
                    buf = io.BytesIO()
                    cropped.save(buf, format="PNG")
                    buf.seek(0)
                    file = discord.File(buf, filename=filename)
                    embed.set_image(url=f"attachment://{filename}")
            elif image_bytes:
                file = discord.File(io.BytesIO(image_bytes), filename=filename)
                embed.set_image(url=f"attachment://{filename}")
            else:
                file = None
        except Exception as e:
            logger.exception("Failed to render gallery image for %s", puzzle_key)
            embed.add_field(name="⚠️ Render Error", value=f"Could not generate puzzle image: `{e}`")
            file = None

        return embed, file

    async def update_message(self, interaction: Optional[Interaction] = None):
        """
        Update the message using the provided interaction (from a button press) or the original interaction
        captured at view creation (for slash-initiated flows).
        """
        self.update_buttons()
        embed, file = await self.generate_embed_and_file()

        target_interaction = interaction or self.interaction
        if target_interaction:
            # If we have an Interaction, edit the original response (slash flow)
            try:
                await target_interaction.edit_original_response(embed=embed, view=self, attachments=[file] if file else [])
                return
            except Exception:
                # fall through to no-interaction path
                pass

        # If no interaction available (prefix flow), attempt to edit the message stored on the view if present.
        # Note: When sending via ctx.send, Discord attaches the view to the message and button clicks will provide
        # an Interaction which we handle above. This fallback is best-effort.
        if hasattr(self, "message") and self.message:
            try:
                await self.message.edit(embed=embed, view=self, attachments=[file] if file else [])
            except Exception:
                logger.debug("Failed to edit stored message for PuzzleGalleryView", exc_info=True)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        # Try to edit the originating response/message to reflect that controls are disabled.
        try:
            if self.interaction:
                await self.interaction.edit_original_response(view=self)
            elif hasattr(self, "message") and self.message:
                await self.message.edit(view=self)
        except discord.NotFound:
            pass
        except Exception:
            logger.debug("Error while timing out PuzzleGalleryView", exc_info=True)

    # PAGINATION BUTTONS
    @discord.ui.button(label="<<", style=discord.ButtonStyle.blurple)
    async def first_page(self, interaction: Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.current_index = 0
        await self.update_message(interaction)

    @discord.ui.button(label="<", style=discord.ButtonStyle.blurple)
    async def prev_page(self, interaction: Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.current_index = max(0, self.current_index - 1)
        await self.update_message(interaction)

    @discord.ui.button(label=">", style=discord.ButtonStyle.blurple)
    async def next_page(self, interaction: Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.current_index = min(len(self.user_puzzle_keys) - 1, self.current_index + 1)
        await self.update_message(interaction)

    @discord.ui.button(label=">>", style=discord.ButtonStyle.blurple)
    async def last_page(self, interaction: Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.current_index = len(self.user_puzzle_keys) - 1
        await self.update_message(interaction)


# -------------------------
# LeaderboardView (kept in views so other cogs can reuse)
# -------------------------
class LeaderboardView(discord.ui.View):
    """Paginated leaderboard view that's styled like the gallery embeds."""

    PAGE_SIZE = 10

    def __init__(self, bot, guild: Optional[discord.Guild], puzzle_key: str, leaderboard_data: List[tuple], page: int = 0):
        super().__init__(timeout=300.0)
        self.bot = bot
        self.guild = guild
        self.puzzle_key = puzzle_key
        self.leaderboard_data = leaderboard_data  # list of (user_id:int, count:int)
        self.page = page
        self.update_buttons()

    def update_buttons(self):
        total_pages = max(1, (len(self.leaderboard_data) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        self.first_button.disabled = self.page == 0
        self.prev_button.disabled = self.page == 0
        self.next_button.disabled = self.page >= total_pages - 1
        self.last_button.disabled = self.page >= total_pages - 1

    async def generate_embed(self) -> discord.Embed:
        meta = PUZZLE_CONFIG.get(self.puzzle_key, {})
        theme_name = meta.get("theme")
        theme = THEMES.get(theme_name) if theme_name else None

        display_name = meta.get("display_name", get_puzzle_display_name(self.bot.data, self.puzzle_key))
        emoji = theme.emoji if theme else Emojis.TROPHY
        color = theme.color if theme else Colors.THEME_COLOR

        total_pages = max(1, (len(self.leaderboard_data) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        start = self.page * self.PAGE_SIZE
        end = start + self.PAGE_SIZE

        lines: List[str] = []
        if not self.leaderboard_data:
            lines.append("No one has collected pieces for this puzzle yet.")
        else:
            for i, (user_id, count) in enumerate(self.leaderboard_data[start:end], start=start + 1):
                try:
                    user = self.bot.get_user(int(user_id)) or await self.bot.fetch_user(int(user_id))
                except Exception:
                    user = None
                mention = user.mention if user else f"User (`{user_id}`)"
                lines.append(f"**{i}.** {mention} — `{count}` pieces")

        # first finisher info
        finishers = self.bot.data.get("puzzle_finishers", {}).get(self.puzzle_key, [])
        if finishers:
            first = finishers[0]
            try:
                first_user = self.bot.get_user(first["user_id"]) or await self.bot.fetch_user(first["user_id"])
                first_line = f"\n**First Finisher:** {first_user.mention} ({first['timestamp']})"
            except Exception:
                first_line = f"\n**First Finisher:** `{first['user_id']}` ({first['timestamp']})"
            lines.append(first_line)

        # reward role info
        role_id = meta.get("reward_role_id")
        if role_id and self.guild:
            role = self.guild.get_role(int(role_id))
            if role:
                lines.append(f"\n**Reward Role:** {role.mention}")
        elif role_id:
            lines.append(f"\n**Reward Role:** <@&{role_id}>")

        embed = discord.Embed(title=f"{emoji} Leaderboard — {display_name}", description="\n".join(lines), color=color)

        if self.guild and self.guild.icon:
            embed.set_author(name=display_name, icon_url=self.guild.icon.url)
        else:
            embed.set_author(name=display_name)

        embed.set_footer(text=f"Page {self.page + 1} of {total_pages}")
        return embed

    @discord.ui.button(label="<<", style=discord.ButtonStyle.gray)
    async def first_button(self, interaction: Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.page = 0
        self.update_buttons()
        await interaction.edit_original_response(embed=await self.generate_embed(), view=self)

    @discord.ui.button(label="<", style=discord.ButtonStyle.blurple)
    async def prev_button(self, interaction: Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        self.page = max(0, self.page - 1)
        self.update_buttons()
        await interaction.edit_original_response(embed=await self.generate_embed(), view=self)

    @discord.ui.button(label=">", style=discord.ButtonStyle.blurple)
    async def next_button(self, interaction: Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        total_pages = max(1, (len(self.leaderboard_data) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        self.page = min(total_pages - 1, self.page + 1)
        self.update_buttons()
        await interaction.edit_original_response(embed=await self.generate_embed(), view=self)

    @discord.ui.button(label=">>", style=discord.ButtonStyle.gray)
    async def last_button(self, interaction: Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        total_pages = max(1, (len(self.leaderboard_data) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        self.page = total_pages - 1
        self.update_buttons()
        await interaction.edit_original_response(embed=await self.generate_embed(), view=self)


# -------------------------
# Helper to open the leaderboard (so commands can call this easily)
# -------------------------
async def open_leaderboard_view(bot, interaction: Interaction, puzzle_key: str):
    """
    Build leaderboard data and send a leaderboard view message.
    This helper replicates the 'leaderboard-in-views' pattern so cogs/commands can call it.
    """
    await interaction.response.defer()
    # Build leaderboard data: list of (user_id:int, count:int)
    all_user_pieces = bot.data.get("user_pieces", {})
    leaderboard_data = [
        (int(user_id), len(user_puzzles.get(puzzle_key, [])))
        for user_id, user_puzzles in all_user_pieces.items()
        if puzzle_key in user_puzzles and len(user_puzzles[puzzle_key]) > 0
    ]
    leaderboard_data.sort(key=lambda x: (-x[1], x[0]))

    view = LeaderboardView(bot, interaction.guild, puzzle_key, leaderboard_data, page=0)
    embed = await view.generate_embed()
    await interaction.followup.send(embed=embed, view=view)