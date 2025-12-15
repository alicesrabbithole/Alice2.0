import io
import logging
import asyncio
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

import discord
from discord import app_commands, Interaction
from discord.ext import commands

from utils.db_utils import (
    add_piece_to_user,
    resolve_puzzle_key,
    get_puzzle_display_name,
    save_data,
    get_user_pieces,
)
from ui.views import PuzzleGalleryView, open_leaderboard_view, LeaderboardView
from utils.theme import Emojis, Colors
from utils.checks import is_admin
from typing import Literal

logger = logging.getLogger(__name__)


class PuzzlesCog(commands.Cog, name="Puzzles"):
    """Commands for viewing puzzle progress and leaderboards."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _reply(
        self,
        ctx: commands.Context,
        content: Optional[str] = None,
        *,
        embed: Optional[discord.Embed] = None,
        file: Optional[discord.File] = None,
        view: Optional[discord.ui.View] = None,
        ephemeral: bool = False,
        mention_author: bool = False,
    ):
        """
        Unified reply helper that works for both prefix (Context) and slash (Interaction) invocations.
        - If invoked via Interaction, prefer interaction.response.send_message (or followup if already responded).
        - Otherwise fall back to ctx.send for prefix commands.
        Accepts content, embed, file, view and ephemeral flag.
        """
        interaction = getattr(ctx, "interaction", None)
        try:
            if interaction and isinstance(interaction, discord.Interaction):
                # If the response isn't used yet, use response.send_message
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        content, embed=embed, file=file, view=view, ephemeral=ephemeral, mention_author=mention_author
                    )
                else:
                    await interaction.followup.send(
                        content, embed=embed, file=file, view=view, ephemeral=ephemeral, mention_author=mention_author
                    )
                return
        except Exception:
            logger.debug("Reply via interaction failed, falling back to ctx.send", exc_info=True)

        # Fallback for prefix context
        try:
            await ctx.send(content, embed=embed, file=file, view=view, mention_author=mention_author)
        except Exception:
            # last resort: try a plain send without extras
            try:
                await ctx.send(content or (embed.title if embed else None))
            except Exception:
                logger.exception("Failed to send message via ctx.send")

    async def puzzle_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        """Autocomplete for puzzle names, showing the display name."""
        puzzles = self.bot.data.get("puzzles", {})
        choices = [
            app_commands.Choice(name=meta.get("display_name", slug), value=slug)
            for slug, meta in puzzles.items()
            if current.lower() in slug.lower() or current.lower() in meta.get("display_name", slug).lower()
        ]
        return choices[:25]

    @commands.hybrid_command(name="gallery", description="Browse through all the puzzles you have started.")
    async def gallery(self, ctx: commands.Context):
        """Shows an interactive gallery of all puzzles — include puzzles with no collected pieces as well."""
        await ctx.defer(ephemeral=False)
        logger.info(f"[DEBUG] /gallery invoked by {ctx.author} ({ctx.author.id})")

        # Build the list of all puzzles (sorted by display name)
        all_puzzles = list(self.bot.data.get("puzzles", {}).keys())
        all_puzzles.sort(key=lambda key: get_puzzle_display_name(self.bot.data, key))

        # Filter out puzzles hidden from members unless the invoker is an admin (manage_guild or guild owner)
        hidden = set(self.bot.data.get("hidden_puzzles", []))
        try:
            is_guild_context = ctx.guild is not None
            is_admin_user = False
            if is_guild_context:
                # treat guild owner and users with manage_guild as admins for visibility purposes
                is_admin_user = ctx.author.id == getattr(ctx.guild, "owner_id", None) or ctx.author.guild_permissions.manage_guild
            # privileged users (always_show_for) bypass hidden filter
            privileged = set(self.bot.data.get("always_show_for", []))
            is_privileged_user = int(ctx.author.id) in {int(x) for x in privileged} if privileged else False

            # If not admin and not privileged, filter hidden puzzles out
            if not (is_admin_user or is_privileged_user) and hidden:
                all_puzzles = [k for k in all_puzzles if k not in hidden]
        except Exception:
            # If anything goes wrong, default to not filtering
            logger.exception("Error while checking admin permissions for gallery filtering")

        all_puzzles.sort(key=lambda key: get_puzzle_display_name(self.bot.data, key))

        # The user's collected puzzle keys (may be absent or empty)
        user_pieces = self.bot.data.get("user_pieces", {})
        user_puzzles = user_pieces.get(str(ctx.author.id), {})

        # Build gallery list: put puzzles the user has any pieces for first, then the rest.
        puzzles_with_pieces = [k for k in all_puzzles if k in user_puzzles and user_puzzles.get(k)]
        puzzles_without_pieces = [k for k in all_puzzles if k not in puzzles_with_pieces]
        user_puzzle_keys = puzzles_with_pieces + puzzles_without_pieces

        # If there are no puzzles configured at all, inform the user.
        if not user_puzzle_keys:
            return await self._reply(ctx, "There are no puzzles configured yet.", ephemeral=True)

        # Pass the interaction when available so the view can edit the original response later.
        interaction = getattr(ctx, "interaction", None)
        view = PuzzleGalleryView(self.bot, interaction, user_puzzle_keys, current_index=0, owner_id=ctx.author.id)
        embed, file = await view.generate_embed_and_file()

        # Send using interaction context if available (slash), otherwise ctx.send works for prefix.
        if interaction:
            await self._reply(ctx, None, embed=embed, file=file, view=view, ephemeral=False)
        else:
            await self._reply(ctx, None, embed=embed, file=file, view=view, ephemeral=False)

    @commands.hybrid_command(name="leaderboard", description="Show the top collectors for a puzzle.")
    @app_commands.autocomplete(puzzle_name=puzzle_autocomplete)
    async def leaderboard(self, ctx: commands.Context, *, puzzle_name: str):
        """Displays the leaderboard for a specific puzzle using the shared LeaderboardView (gallery-styled)."""
        await ctx.defer(ephemeral=False)
        puzzle_key = resolve_puzzle_key(self.bot.data, puzzle_name)
        if not puzzle_key:
            return await self._reply(ctx, f"{Emojis.FAILURE} Puzzle not found: `{puzzle_name}`", ephemeral=True)

        # Hidden puzzles policy:
        # - Admins can always see them (guild owner or manage_guild).
        # - Users listed in bot.data['always_show_for'] can always see them (your exception).
        # - Everyone else sees "not found" for hidden puzzles to avoid leaking existence.
        hidden = set(self.bot.data.get("hidden_puzzles", []))

        # Determine admin permission in this guild (conservative default: not admin)
        is_admin_user = False
        try:
            if ctx.guild is not None:
                is_admin_user = ctx.author.id == getattr(ctx.guild, "owner_id", None) or ctx.author.guild_permissions.manage_guild
        except Exception:
            is_admin_user = False

        # Privileged list from persistent data (list of user IDs)
        privileged = set(self.bot.data.get("always_show_for", []))  # put your user id here to always see
        is_privileged_user = int(ctx.author.id) in {int(x) for x in privileged} if privileged else False

        # If puzzle is hidden, only allow admins or privileged users
        if puzzle_key in hidden and not (is_admin_user or is_privileged_user):
            # Treat as "not found" for non-admin/non-privileged to avoid leaking existence
            return await self._reply(ctx, f"{Emojis.FAILURE} Puzzle not found: `{puzzle_name}`", ephemeral=True)

        # If invoked as a slash command, prefer the interaction-based helper (keeps behavior consistent).
        interaction = getattr(ctx, "interaction", None)
        if interaction:
            # open_leaderboard_view will handle deferring/followup appropriately
            return await open_leaderboard_view(self.bot, interaction, puzzle_key)

        # Fallback for prefix invocation: build leaderboard data and construct the LeaderboardView directly.
        all_user_pieces = self.bot.data.get("user_pieces", {})
        leaderboard_data = [
            (int(user_id), len(user_puzzles.get(puzzle_key, [])))
            for user_id, user_puzzles in all_user_pieces.items()
            if puzzle_key in user_puzzles and len(user_puzzles[puzzle_key]) > 0
        ]
        leaderboard_data.sort(key=lambda x: (-x[1], x[0]))

        # Use the same LeaderboardView from ui.views so the styling/pagination matches the gallery.
        view = LeaderboardView(self.bot, ctx.guild, puzzle_key, leaderboard_data, page=0)
        embed = await view.generate_embed()
        await self._reply(ctx, None, embed=embed, view=view, ephemeral=False)

    @commands.hybrid_command(name="firstfinisher", description="Show who finished a puzzle first!")
    @app_commands.autocomplete(puzzle_name=puzzle_autocomplete)
    async def firstfinisher(self, ctx: commands.Context, *, puzzle_name: str):
        puzzle_key = resolve_puzzle_key(self.bot.data, puzzle_name)
        finishers = self.bot.data.get("puzzle_finishers", {}).get(puzzle_key, [])
        if not finishers:
            return await self._reply(ctx, "No one has completed this puzzle yet!", ephemeral=True)
        first = finishers[0]
        user = self.bot.get_user(first["user_id"]) or await self.bot.fetch_user(first["user_id"])
        await self._reply(
            ctx,
            f"The first person to complete **{get_puzzle_display_name(self.bot.data, puzzle_key)}** was: {user.mention}`!",
            ephemeral=False,
        )

    # -------------------------
    # Admin: toggle hide/unhide and list hidden puzzles (replaces puzzle_hide/puzzle_unhide)
    # -------------------------
    @commands.hybrid_command(name="puzzle_toggle", description="Toggle hide/unhide state for a puzzle (admin only).")
    @app_commands.autocomplete(puzzle_name=puzzle_autocomplete)
    @app_commands.choices(
        action=[
            app_commands.Choice(name="hide", value="hide"),
            app_commands.Choice(name="unhide", value="unhide"),
        ]
    )
    @is_admin()
    async def puzzle_toggle(self, ctx: commands.Context, puzzle_name: str,
                            action: Optional[Literal["hide", "unhide"]] = None):
        """
        Toggle whether a puzzle is hidden from non-admin galleries.

        Usage:
          /puzzle_toggle <puzzle name>            -> toggles current state
          /puzzle_toggle <puzzle name> hide       -> force hide
          /puzzle_toggle <puzzle name> unhide     -> force unhide
        """
        await ctx.defer(ephemeral=True)
        puzzle_key = resolve_puzzle_key(self.bot.data, puzzle_name)
        if not puzzle_key:
            return await self._reply(ctx, f"❌ Puzzle not found: `{puzzle_name}`", ephemeral=True)

        hidden = set(self.bot.data.get("hidden_puzzles", []))

        action_norm = (action or "").strip().lower()
        if action_norm not in ("hide", "unhide", ""):
            return await self._reply(ctx, "Invalid action. Use `hide`, `unhide`, or omit to toggle.", ephemeral=True)

        changed = False
        if action_norm == "hide":
            if puzzle_key in hidden:
                return await self._reply(ctx,
                                         f"ℹ️ Puzzle **{get_puzzle_display_name(self.bot.data, puzzle_key)}** is already hidden.",
                                         ephemeral=True)
            hidden.add(puzzle_key)
            changed = True
            result_msg = f"✅ Hidden puzzle **{get_puzzle_display_name(self.bot.data, puzzle_key)}**."
            action_taken = "hide"
        elif action_norm == "unhide":
            if puzzle_key not in hidden:
                return await self._reply(ctx,
                                         f"ℹ️ Puzzle **{get_puzzle_display_name(self.bot.data, puzzle_key)}** is not hidden.",
                                         ephemeral=True)
            hidden.remove(puzzle_key)
            changed = True
            result_msg = f"✅ Unhidden puzzle **{get_puzzle_display_name(self.bot.data, puzzle_key)}**."
            action_taken = "unhide"
        else:
            # toggle
            if puzzle_key in hidden:
                hidden.remove(puzzle_key)
                changed = True
                result_msg = f"✅ Unhidden puzzle **{get_puzzle_display_name(self.bot.data, puzzle_key)}**."
                action_taken = "unhide"
            else:
                hidden.add(puzzle_key)
                changed = True
                result_msg = f"✅ Hidden puzzle **{get_puzzle_display_name(self.bot.data, puzzle_key)}**."
                action_taken = "hide"

        if changed:
            self.bot.data["hidden_puzzles"] = list(hidden)
            try:
                save_data(self.bot.data)
            except Exception:
                logger.exception("puzzle_toggle: failed to persist hidden_puzzles")

            # Log the action
            try:
                logger.info(
                    "puzzle_toggle: user=%s(%s) action=%s puzzle=%s",
                    getattr(ctx.author, "name", None),
                    getattr(ctx.author, "id", None),
                    action_taken,
                    puzzle_key,
                )
            except Exception:
                logger.exception("puzzle_toggle: logger.info failed")

            # Optional: send a short audit message to configured audit channel (non-blocking)
            audit_id = getattr(self.bot, "audit_log_channel_id", None) or getattr(self.bot, "audit_channel_id", None)
            if audit_id:
                try:
                    audit_ch = self.bot.get_channel(int(audit_id))
                    if audit_ch and isinstance(audit_ch, discord.abc.Messageable):
                        # fire-and-forget so command isn't delayed by channel posting
                        try:
                            asyncio.create_task(
                                audit_ch.send(
                                    f"[Audit] Puzzle `{puzzle_key}` {action_taken}ed by {getattr(ctx.author, 'mention', str(getattr(ctx.author, 'id', 'unknown')))}"
                                )
                            )
                        except Exception:
                            try:
                                await audit_ch.send(
                                    f"[Audit] Puzzle `{puzzle_key}` {action_taken}ed by {getattr(ctx.author, 'mention', str(getattr(ctx.author, 'id', 'unknown')))}"
                                )
                            except Exception:
                                logger.exception("puzzle_toggle: failed to send audit message")
                except Exception:
                    logger.exception("puzzle_toggle: failed to resolve audit channel %r", audit_id)

        await self._reply(ctx, result_msg, ephemeral=True)

    @commands.hybrid_command(name="puzzle_hidden_list", description="List puzzles currently hidden from member galleries.")
    @is_admin()
    async def puzzle_hidden_list(self, ctx: commands.Context):
        """Admin: list which puzzles are hidden."""
        await ctx.defer(ephemeral=True)
        hidden = list(self.bot.data.get("hidden_puzzles", []))
        if not hidden:
            return await self._reply(ctx, "No puzzles are currently hidden.", ephemeral=True)
        lines = []
        for key in hidden:
            display = get_puzzle_display_name(self.bot.data, key)
            lines.append(f"- {display} (`{key}`)")
        # Log that an admin listed hidden puzzles (audit trail)
        try:
            logger.info("puzzle_hidden_list: user=%s(%s) listed %d hidden puzzles", getattr(ctx.author, "name", None), getattr(ctx.author, "id", None), len(hidden))
        except Exception:
            logger.exception("puzzle_hidden_list: logger.info failed")
        await self._reply(ctx, "Hidden puzzles:\n" + "\n".join(lines), ephemeral=True)

    # -------------------------
    # Admin helpers to manage privileged 'always_show_for' list
    # -------------------------
    @commands.hybrid_command(name="always_show_add", description="Allow a user to always see hidden puzzles (admin only).")
    @is_admin()
    async def always_show_add(self, ctx: commands.Context, user: discord.User):
        """Add a user to the always-show list so they can view hidden puzzles."""
        uid = int(user.id)
        self.bot.data.setdefault("always_show_for", [])
        if any(int(x) == uid for x in self.bot.data["always_show_for"]):
            return await self._reply(ctx, f"✅ {user} is already privileged to view hidden puzzles.", ephemeral=True)

        self.bot.data["always_show_for"].append(uid)
        try:
            save_data(self.bot.data)
        except Exception:
            logger.exception("Failed to persist always_show_for list")

        await self._reply(ctx, f"✅ {user} can now see hidden puzzles.", ephemeral=True)

    @commands.hybrid_command(name="always_show_remove", description="Remove a user from the always-show list (admin only).")
    @is_admin()
    async def always_show_remove(self, ctx: commands.Context, user: discord.User):
        """Remove a user from the always-show list."""
        uid = int(user.id)
        current = [int(x) for x in self.bot.data.get("always_show_for", [])]
        if uid not in current:
            return await self._reply(ctx, f"ℹ️ {user} is not in the always-show list.", ephemeral=True)

        self.bot.data["always_show_for"] = [x for x in current if int(x) != uid]
        try:
            save_data(self.bot.data)
        except Exception:
            logger.exception("Failed to persist always_show_for list")

        await self._reply(ctx, f"✅ {user} no longer has privileged access to hidden puzzles.", ephemeral=True)

    @commands.hybrid_command(name="always_show_list", description="List users who can always view hidden puzzles (admin only).")
    @is_admin()
    async def always_show_list(self, ctx: commands.Context):
        """List privileged users who can view hidden puzzles regardless of admin status."""
        raw = self.bot.data.get("always_show_for", [])
        if not raw:
            return await self._reply(ctx, "No users are currently privileged to view hidden puzzles.", ephemeral=True)

        lines = []
        for uid in raw:
            try:
                u = self.bot.get_user(int(uid)) or await self.bot.fetch_user(int(uid))
                lines.append(f"- {u} (`{int(uid)}`)")
            except Exception:
                lines.append(f"- <unknown user> (`{int(uid)}`)")

        await self._reply(ctx, "Privileged users who can always view hidden puzzles:\n" + "\n".join(lines), ephemeral=True)

    # -------------------------
    # Finishes log / overall leaderboard utilities
    # -------------------------
    def _collect_finish_events(self) -> List[Dict[str, Any]]:
        """
        Collect all finish events from bot.data['puzzle_finishers'] into a flat list.
        Each event is a dict: { 'puzzle': str, 'user_id': int, 'position': int, 'ts': Optional[iso-str or None] }
        """
        events: List[Dict[str, Any]] = []
        pf = self.bot.data.get("puzzle_finishers", {}) or {}
        for puzzle_key, finishers in pf.items():
            if not isinstance(finishers, list):
                continue
            for pos, fin in enumerate(finishers, start=1):
                # fin might be an int user_id, or dict {"user_id":..., "ts":...}
                user_id = None
                ts = None
                try:
                    if isinstance(fin, dict):
                        user_id = int(fin.get("user_id"))
                        ts = fin.get("ts") or fin.get("timestamp") or fin.get("time")
                    else:
                        # legacy shape (just user id)
                        user_id = int(fin)
                except Exception:
                    continue
                events.append({"puzzle": puzzle_key, "user_id": user_id, "position": pos, "ts": ts})
        return events

    @commands.hybrid_command(name="finishes_log", description="Export a log of finishers (first-finish list) across all puzzles.")
    @is_admin()
    async def finishes_log(self, ctx: commands.Context, chronological: Optional[bool] = True):
        """
        Export a log (file) with all recorded finish events.
        - chronological=True (default): sorts by timestamp when available (items without timestamps appear grouped by puzzle after).
        - chronological=False: grouped by puzzle in natural stored order.
        """
        await ctx.defer(ephemeral=True)
        events = self._collect_finish_events()

        # If chronological requested and any timestamps present, try to sort by ISO timestamp
        has_ts = any(e.get("ts") for e in events)
        out_lines = []
        header = "timestamp,puzzle_key,puzzle_name,position,user_id,user_display\n"
        out_lines.append(header)

        if chronological and has_ts:
            # Parse ISO timestamps where present; unknown ts -> keep as None and put after dated events
            def _key(ev):
                t = ev.get("ts")
                if not t:
                    return datetime.max.replace(tzinfo=timezone.utc)
                try:
                    # Accept either ISO-string or numeric epoch
                    if isinstance(t, (int, float)):
                        return datetime.fromtimestamp(float(t), tz=timezone.utc)
                    return datetime.fromisoformat(str(t)).astimezone(timezone.utc)
                except Exception:
                    return datetime.max.replace(tzinfo=timezone.utc)
            events_sorted = sorted(events, key=_key)
        else:
            # Group by puzzle then position (preserves stored order)
            events_sorted = sorted(events, key=lambda e: (e["puzzle"], e["position"]))

        # Build lines; try to resolve display name for user/puzzle where possible
        for ev in events_sorted:
            ts = ev.get("ts") or ""
            puzzle_key = ev.get("puzzle", "")
            puzzle_name = (self.bot.data.get("puzzles", {}) or {}).get(puzzle_key, {}).get("display_name") or puzzle_key
            pos = ev.get("position", "")
            uid = ev.get("user_id", "")
            try:
                user = self.bot.get_user(int(uid)) or await self.bot.fetch_user(int(uid))
                user_display = getattr(user, "name", str(uid))
            except Exception:
                user_display = str(uid)
            out_lines.append(f"{ts},{puzzle_key},{puzzle_name},{pos},{uid},{user_display}\n")

        # If too long for message, send as file
        content = "".join(out_lines)
        if len(content) > 1900:
            bio = io.BytesIO(content.encode("utf-8"))
            bio.seek(0)
            file = discord.File(bio, filename="finishes_log.csv")
            await self._reply(ctx, "Here is the finishes log:", file=file, ephemeral=True)
        else:
            await self._reply(ctx, f"```\n{content}\n```", ephemeral=True)

    @commands.hybrid_command(name="finishes_overall", description="Show overall leaderboard of puzzles finished per user.")
    async def finishes_overall(self, ctx: commands.Context):
        """
        Produce an overall leaderboard counting how many distinct puzzles each user finished.
        Visible to everyone (but you can change to @is_admin if you want it restricted).
        """
        await ctx.defer(ephemeral=False)

        pf = self.bot.data.get("puzzle_finishers", {}) or {}
        # build a set-of-puzzles-per-user
        user_puzzles: Dict[int, set] = {}
        for puzzle_key, finishers in pf.items():
            if not isinstance(finishers, list):
                continue
            seen_for_puzzle = set()
            for fin in finishers:
                try:
                    uid = int(fin.get("user_id")) if isinstance(fin, dict) else int(fin)
                except Exception:
                    continue
                # count each user only once per puzzle
                if uid in seen_for_puzzle:
                    continue
                seen_for_puzzle.add(uid)
                user_puzzles.setdefault(uid, set()).add(puzzle_key)

        # Build leaderboard list
        leaderboard = [(uid, len(puzzles)) for uid, puzzles in user_puzzles.items()]
        leaderboard.sort(key=lambda x: (-x[1], x[0]))

        # Prepare output
        lines = []
        lines.append("Overall finishes leaderboard (number of puzzles finished):\n")
        if not leaderboard:
            lines.append("No finishes recorded.\n")
        else:
            for rank, (uid, cnt) in enumerate(leaderboard, start=1):
                try:
                    user = self.bot.get_user(int(uid)) or await self.bot.fetch_user(int(uid))
                    mention = user.mention
                    name = getattr(user, "name", str(uid))
                except Exception:
                    mention = f"`{uid}`"
                    name = str(uid)
                lines.append(f"{rank}. {mention} — {cnt} puzzles\n")

        # If long, attach as file
        out = "".join(lines)
        if len(out) > 1900:
            bio = io.BytesIO(out.encode("utf-8"))
            bio.seek(0)
            file = discord.File(bio, filename="finishes_overall.txt")
            await self._reply(ctx, None, file=file)
        else:
            await self._reply(ctx, f"```\n{out}\n```")

    @commands.hybrid_command(name="finishes_backfill_ts", description="(admin) Backfill missing timestamps on existing finish records.")
    @is_admin()
    async def finishes_backfill_ts(self, ctx: commands.Context, apply: Optional[bool] = False):
        """
        Helper to backfill 'ts' timestamps for existing puzzle_finishers entries that lack them.
        By default this is a dry-run showing how many would be modified. Pass 'apply=True' to commit changes.
        Timestamps will be set to now + small offsets per puzzle position to preserve ordering.
        """
        await ctx.defer(ephemeral=True)
        pf = self.bot.data.get("puzzle_finishers", {}) or {}
        to_update = []
        now = datetime.now(timezone.utc)
        delta_seconds = 0
        for puzzle_key, finishers in pf.items():
            if not isinstance(finishers, list):
                continue
            for pos, fin in enumerate(finishers, start=1):
                # skip if already has ts
                if isinstance(fin, dict) and fin.get("ts"):
                    continue
                suggested_ts = (now + asyncio.timedelta(seconds=delta_seconds)).isoformat() if hasattr(asyncio, "timedelta") else (now).isoformat()
                to_update.append((puzzle_key, pos - 1, suggested_ts))
                delta_seconds += 1

        if not to_update:
            return await self._reply(ctx, "No missing timestamps found; nothing to backfill.", ephemeral=True)

        if not apply:
            return await self._reply(ctx, f"Dry-run: {len(to_update)} finish records would be backfilled. Re-run with apply=True to commit.", ephemeral=True)

        # Apply updates
        for puzzle_key, idx, ts in to_update:
            try:
                fin = self.bot.data["puzzle_finishers"][puzzle_key][idx]
                if isinstance(fin, dict):
                    fin["ts"] = ts
                else:
                    # convert legacy int -> dict with user_id + ts
                    self.bot.data["puzzle_finishers"][puzzle_key][idx] = {"user_id": int(fin), "ts": ts}
            except Exception:
                logger.exception("Failed to backfill finish record %s[%s]", puzzle_key, idx)

        # persist
        try:
            save_data(self.bot.data)
        except Exception:
            logger.exception("Failed to persist after finishes_backfill_ts")

        await self._reply(ctx, f"Backfilled {len(to_update)} finish records with timestamps.", ephemeral=True)

    # -------------------------
    # Finishes by puzzle (sequential per puzzle)
    # -------------------------
    @commands.hybrid_command(name="finishes_by_puzzle", description="List finishers per puzzle (sequential order).")
    @is_admin()
    async def finishes_by_puzzle(self, ctx: commands.Context, *, include_empty: bool = False):
        """
        Show each puzzle and its finishers in sequential order.
        - include_empty: when True, will list puzzles that have no finishers as well.
        """
        await ctx.defer(ephemeral=True)

        puzzles_meta = self.bot.data.get("puzzles", {}) or {}
        finishers_map = self.bot.data.get("puzzle_finishers", {}) or {}

        lines: List[str] = []
        # Use stored puzzle order; fallback to sorted keys if none
        puzzle_keys = list(puzzles_meta.keys()) or sorted(finishers_map.keys())

        for pkey in puzzle_keys:
            display = (puzzles_meta.get(pkey) or {}).get("display_name") or pkey
            fins = finishers_map.get(pkey, []) or []
            if not fins and not include_empty:
                continue

            lines.append(display)
            if not fins:
                lines.append("  (no finishers recorded)")
                lines.append("")  # blank line
                continue

            for pos, fin in enumerate(fins, start=1):
                try:
                    uid = int(fin.get("user_id")) if isinstance(fin, dict) else int(fin)
                except Exception:
                    uid = None
                if uid:
                    try:
                        user = self.bot.get_user(uid) or await self.bot.fetch_user(uid)
                        mention_or_name = user.mention if user else f"`{uid}`"
                    except Exception:
                        mention_or_name = f"`{uid}`"
                else:
                    mention_or_name = "(unknown)"

                lines.append(f"{pos}) {mention_or_name}")
            lines.append("")  # blank line between puzzles

        if not lines:
            return await self._reply(ctx, "No finishers recorded.", ephemeral=True)

        out = "\n".join(lines)
        if len(out) > 1900:
            bio = io.BytesIO(out.encode("utf-8"))
            bio.seek(0)
            file = discord.File(bio, filename="finishes_by_puzzle.txt")
            await self._reply(ctx, "Finishes by puzzle (file):", file=file, ephemeral=True)
        else:
            await self._reply(ctx, f"```\n{out}\n```", ephemeral=True)

    # -------------------------
    # Give item (snowman part or puzzle piece)
    # -------------------------
    @commands.hybrid_command(name="giveitem", description="Give a snowman part or a puzzle piece to a user.")
    @is_admin()
    async def giveitem(self, ctx: commands.Context, member: discord.Member, required: Optional[bool] = False, item_type: str = None, key: str = None, spec: str = None):
        """
        Give an item to a user.

        Usage examples:
          /giveitem @User false snowman alice hat
          /giveitem @User false puzzle winterwonderland 12

        Parameters:
        - member: target user (mention)
        - required: boolean flag (optional) — unused by default, reserved for integration with StockingCog
        - item_type: "snowman" (or "stocking") or "puzzle"
        - key: buildable key (for snowman) or puzzle key/name (for puzzle)
        - spec: for snowman -> part_key (e.g., "hat"); for puzzle -> piece id (string or number)
        """
        await ctx.defer(ephemeral=True)

        # Basic param checks
        if not item_type or not key or not spec:
            return await self._reply(
                ctx,
                "Usage: /giveitem @user <required:bool> <snowman|puzzle> <buildable_or_puzzle_key> <part_or_piece_id>",
                ephemeral=True,
            )

        itype = item_type.strip().lower()
        # SNOWMAN / buildable path
        if itype in ("snowman", "stocking", "buildable"):
            stocking_cog = self.bot.get_cog("StockingCog")
            if not stocking_cog:
                return await self._reply(ctx, "Stocking cog is not available on this bot (cannot give snowman parts).", ephemeral=True)

            buildable_key = key.strip()
            part_key = spec.strip()

            # Optional: validate buildable/part against stocking_cog _buildables_def if available
            try:
                buildables_def = getattr(stocking_cog, "_buildables_def", {}) or {}
                if buildables_def and buildable_key not in buildables_def:
                    return await self._reply(ctx, f"Unknown buildable '{buildable_key}'. Valid keys: {', '.join(sorted(buildables_def.keys()))}", ephemeral=True)
                if buildables_def and part_key not in (buildables_def.get(buildable_key, {}).get("parts") or {}):
                    return await self._reply(ctx, f"Unknown part '{part_key}' for buildable '{buildable_key}'. Valid parts: {', '.join(sorted((buildables_def.get(buildable_key,{}).get('parts') or {}).keys()))}", ephemeral=True)
            except Exception:
                pass

            try:
                # prefer award_part API if present
                if hasattr(stocking_cog, "award_part"):
                    awarded = await getattr(stocking_cog, "award_part")(int(member.id), buildable_key, part_key, ctx.channel, announce=True)
                elif hasattr(stocking_cog, "award_sticker"):
                    awarded = await getattr(stocking_cog, "award_sticker")(int(member.id), part_key, None, announce=True)
                else:
                    return await self._reply(ctx, "StockingCog does not expose an award API I can call.", ephemeral=True)
            except Exception:
                logger.exception("giveitem: award_part call failed")
                return await self._reply(ctx, "Failed to give snowman part due to an internal error. See logs.", ephemeral=True)

            if awarded:
                return await self._reply(ctx, f"✅ Gave {part_key} ({buildable_key}) to {member.mention}.", ephemeral=True)
            else:
                return await self._reply(ctx, f"ℹ️ {member.mention} already had that part or the award was skipped.", ephemeral=True)

        # PUZZLE path
        elif itype in ("puzzle", "piece", "puzz"):
            puzzle_key_raw = key.strip()
            piece_id = spec.strip()

            # resolve puzzle key (accept display name or key)
            resolved = resolve_puzzle_key(self.bot.data, puzzle_key_raw)
            if not resolved:
                return await self._reply(ctx, f"Puzzle not found: `{puzzle_key_raw}`", ephemeral=True)
            puzzle_key = resolved

            # validate piece exists in pieces registry (if you maintain pieces per puzzle)
            pieces_map = (self.bot.data.get("pieces", {}) or {}).get(puzzle_key, {}) or {}
            piece_exists = False
            try:
                if pieces_map and piece_id in pieces_map:
                    piece_exists = True
                else:
                    # allow numeric index if pieces are keyed by numbers
                    if pieces_map and all(str(k).isdigit() for k in pieces_map.keys()):
                        if str(int(piece_id)) in pieces_map:
                            piece_exists = True
            except Exception:
                piece_exists = False

            if pieces_map and not piece_exists:
                return await self._reply(ctx, f"Piece id `{piece_id}` not found for puzzle `{puzzle_key}`.", ephemeral=True)

            try:
                added = add_piece_to_user(self.bot.data, int(member.id), puzzle_key, piece_id)
            except Exception:
                logger.exception("giveitem: add_piece_to_user failed")
                return await self._reply(ctx, "Failed to grant puzzle piece due to internal error.", ephemeral=True)

            if not added:
                return await self._reply(ctx, f"ℹ️ {member.mention} already has piece `{piece_id}` for puzzle `{puzzle_key}`.", ephemeral=True)

            # persist
            try:
                save_data(self.bot.data)
            except Exception:
                logger.exception("giveitem: failed to persist data after add_piece_to_user")

            # try to trigger completion awarding helper if available (best-effort)
            try:
                from ui.views import _attempt_award_completion  # type: ignore
                interaction = getattr(ctx, "interaction", None)
                if interaction:
                    try:
                        awarded, reason = await _attempt_award_completion(interaction, self.bot, puzzle_key, int(member.id))
                        if awarded:
                            return await self._reply(ctx, f"✅ Granted piece `{piece_id}` to {member.mention} and awarded completion rewards.", ephemeral=True)
                    except Exception:
                        logger.exception("giveitem: _attempt_award_completion raised")
            except Exception:
                # helper not present or failed to import — that's fine
                pass

            return await self._reply(ctx, f"✅ Granted piece `{piece_id}` for puzzle `{puzzle_key}` to {member.mention}.", ephemeral=True)

        else:
            return await self._reply(ctx, "Unknown item_type — expected 'snowman' or 'puzzle'.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(PuzzlesCog(bot))