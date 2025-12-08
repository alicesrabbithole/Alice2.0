import io
import logging
import asyncio
from typing import Optional, List, Tuple, Dict

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

# Small lock to avoid racing awards when multiple collectors act at once
_award_lock = asyncio.Lock()


async def _attempt_award_completion(interaction: discord.Interaction, bot: discord.Client, puzzle_key: str, user_id: int):
    """
    Safe helper to attempt awarding the completion/reward role and sending a congrats followup.
    Uses a lock to avoid racing multiple concurrent collectors.
    Returns: (awarded: bool, reason: str)
    """
    async with _award_lock:
        # Prefer runtime-loaded meta but fall back to static PUZZLE_CONFIG if not present
        meta = (bot.data.get("puzzles", {}) or {}).get(puzzle_key) or PUZZLE_CONFIG.get(puzzle_key, {}) or {}
        role_id = meta.get("completion_role_id") or meta.get("reward_role_id") or meta.get("reward_role")
        try:
            if role_id is not None:
                role_id = int(role_id)
        except (ValueError, TypeError):
            logger.warning("Invalid role id in meta for puzzle %s: %r", puzzle_key, role_id)
            role_id = None

        # Recompute pieces and totals after save
        user_pieces = get_user_pieces(bot.data, user_id, puzzle_key)
        total_pieces = len(bot.data.get("pieces", {}).get(puzzle_key, {}))

        logger.debug(
            "Award check (helper): puzzle=%s user=%s user_pieces=%s total=%s role_id=%r meta_keys=%s",
            puzzle_key,
            user_id,
            len(user_pieces),
            total_pieces,
            role_id,
            list(meta.keys()) if isinstance(meta, dict) else None,
        )

        if len(user_pieces) < total_pieces:
            return False, "not complete"

        if not role_id:
            return False, "no role configured"

        guild = getattr(interaction, "guild", None)
        if not guild:
            return False, "no guild context"

        role_obj = guild.get_role(role_id)
        if not role_obj:
            logger.warning(
                "Configured role id %r for puzzle %s not found in guild %s",
                role_id,
                puzzle_key,
                getattr(guild, "id", None),
            )
            return False, "role not found in guild"

        try:
            member = guild.get_member(user_id) or await guild.fetch_member(user_id)
        except Exception:
            logger.exception("Could not fetch member %s for guild %s", user_id, getattr(guild, "id", None))
            return False, "member not in guild"

        if role_obj in member.roles:
            return False, "already has role"

        try:
            await member.add_roles(role_obj, reason=f"Completed puzzle: {meta.get('display_name', puzzle_key)}")
            # Send congrats using followup (safe after response or defer)
            try:
                await interaction.followup.send(
                    f"🏆 Congratulations! You completed **{meta.get('display_name', puzzle_key)}** and earned the {role_obj.mention} role!",
                    ephemeral=True,
                )
            except Exception:
                # followup may fail if not deferred; attempt response if available
                try:
                    if not interaction.response.is_done():
                        await interaction.response.send_message(
                            f"🏆 Congratulations! You completed **{meta.get('display_name', puzzle_key)}** and earned the {role_obj.mention} role!",
                            ephemeral=True,
                        )
                except Exception:
                    logger.debug("Could not send congrats via followup or response", exc_info=True)

            logger.info("Awarded role %s to user %s for puzzle %s", role_id, user_id, puzzle_key)
            return True, "awarded"
        except Exception as e:
            logger.exception(
                "Failed to add completion role %s to user %s for puzzle %s: %s",
                role_id,
                user_id,
                puzzle_key,
                e,
            )
            try:
                await interaction.followup.send(
                    "⚠️ I couldn't add the completion role — please check my Manage Roles permission and role hierarchy.",
                    ephemeral=True,
                )
            except Exception:
                pass
            return False, "add_roles failed"


# -----------------------------------------------------
# DropView - used by puzzle_drops_cog (kept here for import)
# -----------------------------------------------------
class DropView(discord.ui.View):
    """The view for a puzzle piece drop, containing the 'Collect' button."""

    def __init__(
        self,
        bot,
        puzzle_key: str,
        puzzle_display_name: str,
        piece_id: str,
        claim_limit: int,
        button_color=None,
        timeout: float = 30.0,
    ):
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
                logger.warning(
                    f"Could not parse custom emoji: {config.CUSTOM_EMOJI_STRING}. Falling back to default."
                )
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

        # Persist state immediately
        save_data(self.bot.data)
        self.claimants.append(interaction.user)

        # Acknowledge the collect to the claimer
        try:
            await interaction.response.send_message(
                f"✅ You collected Piece `{self.piece_id}` for the **{self.puzzle_display_name}** puzzle!",
                ephemeral=True,
            )
        except Exception:
            # If response already used, attempt followup (best-effort)
            try:
                await interaction.followup.send(
                    f"✅ You collected Piece `{self.piece_id}` for the **{self.puzzle_display_name}** puzzle!",
                    ephemeral=True,
                )
            except Exception:
                logger.debug("Failed to ack collect via response or followup", exc_info=True)

        # ==== Reward Role, Finisher, and Logging ====
        # Run awarding helper (will re-check completeness and handle messaging)
        try:
            awarded, reason = await _attempt_award_completion(interaction, self.bot, self.puzzle_key, interaction.user.id)
            logger.debug("Award attempt result for %s on %s: %s", interaction.user.id, self.puzzle_key, (awarded, reason))
        except Exception:
            logger.exception("Error while attempting to award completion for %s on %s", interaction.user.id, self.puzzle_key)

        # Track first finisher (persisted) — store only user_id (no timestamp)
        meta = PUZZLE_CONFIG.get(self.puzzle_key, {})
        user_id = interaction.user.id
        user_pieces = get_user_pieces(self.bot.data, user_id, self.puzzle_key)
        total_pieces = len(self.bot.data.get("pieces", {}).get(self.puzzle_key, {}))
        if len(user_pieces) == total_pieces:
            finishers = self.bot.data.setdefault("puzzle_finishers", {}).setdefault(self.puzzle_key, [])
            if user_id not in [f.get("user_id") for f in finishers]:
                finishers.append({"user_id": user_id})
                save_data(self.bot.data)

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
            mentions = ", ".join(u.mention for u in self.claimants)
            summary = f"Piece `{self.piece_id}` of the **{self.puzzle_display_name}** puzzle was collected by: {mentions}"
        try:
            await self.message.channel.send(summary, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            pass


# -------------------------
# PuzzleGalleryView
# -------------------------
class PuzzleGalleryView(discord.ui.View):
    """A paginated view for browsing a user's collected puzzles.

    Controls are restricted to the opener (the user who invoked the gallery) by default.
    If you want to allow everyone to control the view, instantiate with opener_id=None.
    """

    # The renderer no longer draws the progress bar; no cropping required.
    PROGRESS_BAR_CROP_HEIGHT = 0

    def __init__(
        self,
        bot,
        interaction: Optional[Interaction],
        user_puzzle_keys: list[str],
        current_index: int = 0,
        owner_id: Optional[int] = None,
        opener_id: Optional[int] = None,
    ):
        super().__init__(timeout=300.0)
        self.bot = bot
        # The Interaction provided when the view was created (may be None for prefix sends)
        self.interaction = interaction
        self.user_puzzle_keys = user_puzzle_keys
        self.current_index = current_index

        # Owner of the gallery (whose pieces are shown)
        self.owner_id = owner_id or (interaction.user.id if interaction and interaction.user else None)

        # The opener (viewer) who initially invoked the gallery; kept to restrict controls.
        # If None, anyone can control the view.
        self.opener_id = opener_id or (interaction.user.id if interaction and interaction.user else None)

        self.update_buttons()

    def update_buttons(self):
        """Enable/disable pagination buttons based on the current index."""
        self.first_page.disabled = self.current_index == 0
        self.prev_page.disabled = self.current_index == 0
        self.next_page.disabled = self.current_index >= len(self.user_puzzle_keys) - 1
        self.last_page.disabled = self.current_index >= len(self.user_puzzle_keys) - 1

    async def _deny_if_not_opener(self, interaction: Interaction) -> bool:
        """Return True if denied (and a denial message already sent)."""
        if self.opener_id is None:
            return False
        if interaction.user and interaction.user.id == self.opener_id:
            return False
        # Quick ephemeral reply denying control
        try:
            await interaction.response.send_message("Only the gallery opener can use these controls.", ephemeral=True)
        except Exception:
            # If response already used, try followup
            try:
                await interaction.followup.send("Only the gallery opener can use these controls.", ephemeral=True)
            except Exception:
                logger.debug("Failed to notify non-opener about control restriction", exc_info=True)
        return True

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

        emoji = (
            theme.emoji
            if theme
            else (config.CUSTOM_EMOJI_STRING if hasattr(config, "CUSTOM_EMOJI_STRING") else Emojis.PUZZLE_PIECE)
        )
        embed_color = theme.color if theme else Colors.THEME_COLOR

        desc = f"**Progress:** {len(user_pieces)} / {total_pieces} pieces collected."

        # Add first finisher info
        finishers = self.bot.data.get("puzzle_finishers", {}).get(puzzle_key, [])
        if finishers:
            first = finishers[0]
            try:
                first_user = self.bot.get_user(first["user_id"]) or await self.bot.fetch_user(first["user_id"])
                # Display only the user mention (no timestamp available)
                desc += f"\n**First Finisher:** {first_user.mention}"
            except Exception:
                desc += f"\n**First Finisher:** `{first.get('user_id')}`"

        # Add reward role info (read-only display) - accept either key
        role_id_display = meta.get("completion_role_id") or meta.get("reward_role_id") or meta.get("reward_role")
        role_text = ""
        if role_id_display and self.interaction and self.interaction.guild:
            try:
                role = self.interaction.guild.get_role(int(role_id_display))
                if role:
                    role_text = f"\n**Reward Role:** {role.mention}"
            except Exception:
                role_text = f"\n**Reward Role:** <@&{role_id_display}>"
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
                embed.set_author(
                    name=self.interaction.user.display_name, icon_url=self.interaction.user.display_avatar.url
                )
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

            # No cropping needed because renderer doesn't draw progress bar.
            if image_bytes:
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
        if await self._deny_if_not_opener(interaction):
            return
        await interaction.response.defer()
        self.current_index = 0
        await self.update_message(interaction)

    @discord.ui.button(label="<", style=discord.ButtonStyle.blurple)
    async def prev_page(self, interaction: Interaction, button: discord.ui.Button):
        if await self._deny_if_not_opener(interaction):
            return
        await interaction.response.defer()
        self.current_index = max(0, self.current_index - 1)
        await self.update_message(interaction)

    @discord.ui.button(label=">", style=discord.ButtonStyle.blurple)
    async def next_page(self, interaction: Interaction, button: discord.ui.Button):
        if await self._deny_if_not_opener(interaction):
            return
        await interaction.response.defer()
        self.current_index = min(len(self.user_puzzle_keys) - 1, self.current_index + 1)
        await self.update_message(interaction)

    @discord.ui.button(label=">>", style=discord.ButtonStyle.blurple)
    async def last_page(self, interaction: Interaction, button: discord.ui.Button):
        if await self._deny_if_not_opener(interaction):
            return
        await interaction.response.defer()
        self.current_index = len(self.user_puzzle_keys) - 1
        await self.update_message(interaction)


# -------------------------
# LeaderboardView (kept in views so other cogs can reuse)
# -------------------------
class LeaderboardView(discord.ui.View):
    """Paginated leaderboard view that's styled like the gallery embeds.

    Controls can be restricted to an opener by setting opener_id on the view instance.
    """

    PAGE_SIZE = 10

    def __init__(self, bot, guild: Optional[discord.Guild], puzzle_key: str, leaderboard_data: List[tuple], page: int = 0, opener_id: Optional[int] = None):
        super().__init__(timeout=300.0)
        self.bot = bot
        self.guild = guild
        self.puzzle_key = puzzle_key
        self.leaderboard_data = leaderboard_data  # list of (user_id:int, count:int)
        self.page = page
        # Restrict interaction to this user if provided (None = allow everyone)
        self.opener_id = opener_id
        self.update_buttons()

    def update_buttons(self):
        total_pages = max(1, (len(self.leaderboard_data) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        self.first_button.disabled = self.page == 0
        self.prev_button.disabled = self.page == 0
        self.next_button.disabled = self.page >= total_pages - 1
        self.last_button.disabled = self.page >= total_pages - 1

    async def _deny_if_not_opener(self, interaction: Interaction) -> bool:
        """Return True if denied (and a denial message already sent)."""
        if self.opener_id is None:
            return False
        if interaction.user and interaction.user.id == self.opener_id:
            return False
        try:
            await interaction.response.send_message("Only the opener can use these controls.", ephemeral=True)
        except Exception:
            try:
                await interaction.followup.send("Only the opener can use these controls.", ephemeral=True)
            except Exception:
                logger.debug("Failed to notify non-opener about control restriction", exc_info=True)
        return True

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
                first_line = f"\n**First Finisher:** {first_user.mention}"
            except Exception:
                first_line = f"\n**First Finisher:** `{first['user_id']}`"
            lines.append(first_line)

        # reward role info (display only)
        role_id_display = meta.get("completion_role_id") or meta.get("reward_role_id") or meta.get("reward_role")
        if role_id_display and self.guild:
            try:
                role = self.guild.get_role(int(role_id_display))
                if role:
                    lines.append(f"\n**Reward Role:** {role.mention}")
            except Exception:
                lines.append(f"\n**Reward Role:** <@&{role_id_display}>")
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
        if await self._deny_if_not_opener(interaction):
            return
        await interaction.response.defer()
        self.page = 0
        self.update_buttons()
        await interaction.edit_original_response(embed=await self.generate_embed(), view=self)

    @discord.ui.button(label="<", style=discord.ButtonStyle.blurple)
    async def prev_button(self, interaction: Interaction, button: discord.ui.Button):
        if await self._deny_if_not_opener(interaction):
            return
        await interaction.response.defer()
        self.page = max(0, self.page - 1)
        self.update_buttons()
        await interaction.edit_original_response(embed=await self.generate_embed(), view=self)

    @discord.ui.button(label=">", style=discord.ButtonStyle.blurple)
    async def next_button(self, interaction: Interaction, button: discord.ui.Button):
        if await self._deny_if_not_opener(interaction):
            return
        await interaction.response.defer()
        total_pages = max(1, (len(self.leaderboard_data) + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        self.page = min(total_pages - 1, self.page + 1)
        self.update_buttons()
        await interaction.edit_original_response(embed=await self.generate_embed(), view=self)

    @discord.ui.button(label=">>", style=discord.ButtonStyle.gray)
    async def last_button(self, interaction: Interaction, button: discord.ui.Button):
        if await self._deny_if_not_opener(interaction):
            return
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
    This helper only defers when safe and attaches opener_id so only the opener can
    control the leaderboard view by default.
    """
    try:
        # Only defer if the interaction hasn't been responded to already.
        if not interaction.response.is_done():
            await interaction.response.defer()
    except Exception:
        logger.debug("open_leaderboard_view: could not defer or already responded", exc_info=True)

    # Build leaderboard data: list of (user_id:int, count:int)
    all_user_pieces = bot.data.get("user_pieces", {})
    leaderboard_data = [
        (int(user_id), len(user_puzzles.get(puzzle_key, [])))
        for user_id, user_puzzles in all_user_pieces.items()
        if puzzle_key in user_puzzles and len(user_puzzles[puzzle_key]) > 0
    ]
    leaderboard_data.sort(key=lambda x: (-x[1], x[0]))

    view = LeaderboardView(bot, interaction.guild, puzzle_key, leaderboard_data, page=0, opener_id=(interaction.user.id if interaction and interaction.user else None))
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