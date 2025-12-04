import io
import logging
from typing import Optional, List, Tuple

import discord
from discord import Interaction
from PIL import Image as PILImage

import config
from utils.db_utils import (
    add_piece_to_user,
    save_data,
    get_puzzle_display_name,
    get_user_pieces,
)
from .overlay import render_progress_image
from utils.theme import Emojis, Colors, THEMES, PUZZLE_CONFIG

logger = logging.getLogger(__name__)


# -----------------------------------------------------
# DropView - used by puzzle_drops_cog (kept here for import)
# -----------------------------------------------------
class DropView(discord.ui.View):
    """The view for a puzzle piece drop, containing the 'Collect' button."""

    def __init__(self, bot, puzzle_key: str, puzzle_display_name: str, piece_id: str, claim_limit: int, button_color=None, timeout: float = 30.0):
        super().__init__(timeout=timeout)
        self.bot = bot
        self.puzzle_key = puzzle_key
        self.puzzle_display_name = puzzle_display_name
        self.piece_id = piece_id
        self.claim_limit = claim_limit
        self.claimants: List[discord.User] = []
        self.message: Optional[discord.Message] = None
        self.summary_sent = False  # Only allows posting once

        # Set emoji for the button (safely)
        try:
            self.collect_button.emoji = self._get_partial_emoji()
        except Exception:
            # If the button doesn't exist yet (edge cases) ignore
            pass

    def _get_partial_emoji(self) -> discord.PartialEmoji:
        """Safely parses the custom emoji string."""
        if getattr(config, "CUSTOM_EMOJI_STRING", None):
            try:
                return discord.PartialEmoji.from_str(config.CUSTOM_EMOJI_STRING)
            except (TypeError, ValueError):
                logger.warning(f"Could not parse custom emoji: {config.CUSTOM_EMOJI_STRING}. Falling back to default.")
        return discord.PartialEmoji(name=getattr(config, "DEFAULT_EMOJI", "🧩"))

    async def on_timeout(self):
        # Remove the button from the view before editing so it shows as expired.
        try:
            self.remove_item(self.collect_button)
        except Exception:
            pass
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.NotFound:
                pass
        await self.post_summary()
        self.stop()

    @discord.ui.button(label="Collect Piece", style=discord.ButtonStyle.primary)
    async def collect_button(self, interaction: Interaction, button: discord.ui.Button):
        # Try to add piece to user; add_piece_to_user returns False if user already has it.
        if not add_piece_to_user(self.bot.data, interaction.user.id, self.puzzle_key, self.piece_id):
            return await interaction.response.send_message("You already have this piece!", ephemeral=True)

        save_data(self.bot.data)
        self.claimants.append(interaction.user)

        await interaction.response.send_message(
            f"✅ You collected Piece `{self.piece_id}` for the **{self.puzzle_display_name}** puzzle!", ephemeral=True
        )

        # ==== Reward Role, Finisher, and Logging ====
        meta = PUZZLE_CONFIG.get(self.puzzle_key, {})
        # Backwards-compatible role lookup (accept completion_role_id OR reward_role_id)
        role_id = meta.get("completion_role_id") or meta.get("reward_role_id") or meta.get("reward_role")
        try:
            if role_id is not None:
                role_id = int(role_id)
        except (ValueError, TypeError):
            logger.warning("Invalid role id in meta for puzzle %s: %r", self.puzzle_key, role_id)
            role_id = None

        user_id = interaction.user.id
        user_pieces = get_user_pieces(self.bot.data, user_id, self.puzzle_key)
        total_pieces = len(self.bot.data.get("pieces", {}).get(self.puzzle_key, {}))

        logger.debug("Award check (DropView): puzzle=%s user=%s user_pieces=%s total=%s role_id=%r",
                     self.puzzle_key, user_id, len(user_pieces), total_pieces, role_id)

        if len(user_pieces) == total_pieces:
            # Award completion/reward role
            if role_id and interaction.guild:
                role = interaction.guild.get_role(role_id)
                logger.debug("Attempting to award role: role_id=%r role_obj=%r user_roles=%s guild=%s",
                             role_id, role, [r.id for r in interaction.user.roles], interaction.guild.id if interaction.guild else None)
                if role and role not in interaction.user.roles:
                    try:
                        await interaction.user.add_roles(role, reason=f"Completed puzzle: {meta.get('display_name', self.puzzle_key)}")
                        await interaction.followup.send(
                            f"🏆 Congratulations! You completed **{meta.get('display_name', self.puzzle_key)}** and earned the {role.mention} role!",
                            ephemeral=True,
                        )
                    except Exception as e:
                        logger.exception("Failed to add completion role %s to user %s for puzzle %s: %s", role_id, user_id, self.puzzle_key, e)
                        await interaction.followup.send(
                            "⚠️ I couldn't add the completion role — please check my Manage Roles permission and role hierarchy.",
                            ephemeral=True,
                        )
                    # Log to configured channel if present
                    log_channel_id = meta.get("completion_log_channel") or meta.get("log_channel") or 1411859714144468992
                    log_channel = None
                    try:
                        log_channel = interaction.guild.get_channel(log_channel_id)
                        if not log_channel:
                            log_channel = await interaction.guild.fetch_channel(log_channel_id)
                    except Exception:
                        log_channel = None
                    if log_channel:
                        try:
                            await log_channel.send(
                                f"📝 {interaction.user.mention} completed **{meta.get('display_name', self.puzzle_key)}** and was awarded {role.mention}."
                            )
                        except Exception:
                            logger.debug("Failed to write completion log for puzzle %s", self.puzzle_key, exc_info=True)

            # Track first finisher
            finishers = self.bot.data.setdefault("puzzle_finishers", {}).setdefault(self.puzzle_key, [])
            if user_id not in [f.get("user_id") for f in finishers]:
                import datetime
                finishers.append({"user_id": user_id, "timestamp": datetime.datetime.utcnow().isoformat()})
                save_data(self.bot.data)
        # ==== End Reward Role, Finisher, and Logging ====

        # If we've hit claim limit, remove the button, edit, post summary and stop.
        if len(self.claimants) >= self.claim_limit:
            try:
                self.remove_item(button)
            except Exception:
                pass
            if self.message:
                try:
                    await self.message.edit(view=self)
                except discord.NotFound:
                    pass
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


# -------------------------
# PuzzleGalleryView
# -------------------------
class PuzzleGalleryView(discord.ui.View):
    """A paginated view for browsing a user's collected puzzles."""

    # Pixels to crop from the bottom of the generated image to remove the progress bar.
    # When the renderer no longer draws the progress bar this can be 0.
    PROGRESS_BAR_CROP_HEIGHT = 0

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

        # Add reward role info (read-only display) - accept either key
        role_id_display = meta.get("completion_role_id") or meta.get("reward_role_id") or meta.get("reward_role")
        role_text = ""
        if role_id_display and self.interaction and self.interaction.guild:
            role = self.interaction.guild.get_role(int(role_id_display))
            if role:
                role_text = f"\n**Reward Role:** {role.mention}"
        elif role_id_display:
            role_text = f"\n**Reward Role:** <@&{role_id_display}>"

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

            # If renderer no longer draws the progress bar, no cropping is needed.
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

        # reward role info (display only)
        role_id_display = meta.get("completion_role_id") or meta.get("reward_role_id") or meta.get("reward_role")
        if role_id_display and self.guild:
            role = self.guild.get_role(int(role_id_display))
            if role:
                lines.append(f"\n**Reward Role:** {role.mention}")
        elif role_id_display:
            lines.append(f"\n**Reward Role:** <@&{role_id_display}>")

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
    This helper used to call interaction.response.defer() unconditionally and that caused
    InteractionResponded when the caller already deferred.  Now we only defer if the
    interaction hasn't been responded to yet.
    """
    try:
        # Only defer if the interaction hasn't been responded to already.
        if not interaction.response.is_done():
            await interaction.response.defer()
    except Exception:
        # If something odd happens, log and continue to attempt a followup.
        logger.debug("open_leaderboard_view: could not defer or already responded", exc_info=True)

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

    # Use followup (works after defer or if interaction already responded)
    try:
        await interaction.followup.send(embed=embed, view=view)
    except Exception as e:
        # As a fallback, try to send a normal response if followup fails (best-effort).
        logger.exception("open_leaderboard_view: followup send failed, attempting response.send_message: %s", e)
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(embed=embed, view=view)
        except Exception:
            logger.exception("open_leaderboard_view: response.send_message also failed", exc_info=True)