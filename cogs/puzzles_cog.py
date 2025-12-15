import io
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any, Literal

import discord
from discord import app_commands, Interaction
from discord.ext import commands
from discord.ui import View, button, Button

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

logger = logging.getLogger(__name__)


# Module-level autocomplete helper — referenced at decoration time.
async def _puzzle_autocomplete(interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
    bot = getattr(interaction, "client", None)
    if bot is None:
        return []
    puzzles = (getattr(bot, "data", {}) or {}).get("puzzles", {}) or {}
    choices = [
        app_commands.Choice(name=(meta.get("display_name") or slug), value=slug)
        for slug, meta in puzzles.items()
        if current.lower() in slug.lower() or current.lower() in (meta.get("display_name") or "").lower()
    ]
    return choices[:25]


class ConfirmView(View):
    """
    Simple confirm/cancel view.
    - Use from a command like:
        view = ConfirmView(author_id=ctx.author.id)
        await self._reply(ctx, "Are you sure?", view=view, ephemeral=True)
        confirmed = await view.wait_for_result()
    """
    def __init__(self, author_id: Optional[int] = None, timeout: float = 30.0):
        super().__init__(timeout=timeout)
        self.value: Optional[bool] = None
        self.author_id = author_id
        self._result_event = asyncio.Event()

    async def wait_for_result(self) -> bool:
        await self._result_event.wait()
        return bool(self.value)

    async def _finish(self, interaction: Optional[discord.Interaction], confirmed: bool, edit_note: Optional[str] = None):
        self.value = confirmed
        for child in self.children:
            child.disabled = True
        # Try to edit the original message when possible.
        try:
            if interaction is not None:
                try:
                    await interaction.response.edit_message(content=(edit_note or interaction.message.content), view=self)
                except Exception:
                    # If editing fails, attempt a followup to inform user.
                    try:
                        await interaction.followup.send(edit_note or ("Confirmed" if confirmed else "Cancelled"), ephemeral=True)
                    except Exception:
                        pass
        except Exception:
            logger.exception("ConfirmView._finish: error editing/followup")
        finally:
            self._result_event.set()

    @button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm_button(self, interaction: discord.Interaction, button: Button):
        if self.author_id and interaction.user.id != int(self.author_id):
            return await interaction.response.send_message("You cannot confirm this action.", ephemeral=True)
        await self._finish(interaction, True, edit_note="✅ Confirmed — performing action...")

    @button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: Button):
        if self.author_id and interaction.user.id != int(self.author_id):
            return await interaction.response.send_message("You cannot cancel this action.", ephemeral=True)
        await self._finish(interaction, False, edit_note="❌ Cancelled")

    async def on_timeout(self):
        # Timeout: treat as cancelled
        self.value = False
        for child in self.children:
            child.disabled = True
        self._result_event.set()


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
        Unified reply helper for both prefix (Context) and slash (Interaction) invocations.
        - For Interactions: uses interaction.response.send_message or interaction.followup.send.
        - For prefix ctx: uses ctx.send (ephemeral is ignored for prefix).
        """
        interaction = getattr(ctx, "interaction", None)
        try:
            if interaction and isinstance(interaction, discord.Interaction):
                if not interaction.response.is_done():
                    await interaction.response.send_message(content, embed=embed, file=file, view=view, ephemeral=ephemeral)
                else:
                    await interaction.followup.send(content, embed=embed, file=file, view=view, ephemeral=ephemeral)
                return
        except Exception:
            logger.debug("_reply: interaction send failed, falling back to ctx.send", exc_info=True)

        # Fallback for prefix context (ctx.send doesn't accept ephemeral)
        try:
            await ctx.send(content, embed=embed, file=file, view=view)
        except Exception:
            try:
                # As a last resort, send plain content
                await ctx.send(content or (embed.title if embed else None))
            except Exception:
                logger.exception("_reply: failed to send via ctx.send")

    async def _confirm(self, ctx: commands.Context, prompt: str, *, timeout: float = 30.0) -> bool:
        """
        Show a ConfirmView to the invoking user and return True if they confirmed.
        """
        author_id = getattr(ctx.author, "id", None)
        view = ConfirmView(author_id=author_id, timeout=timeout)
        # Send ephemeral prompt when possible
        await self._reply(ctx, prompt, view=view, ephemeral=True)
        try:
            confirmed = await view.wait_for_result()
            return bool(confirmed)
        except Exception:
            logger.exception("_confirm: waiting for confirmation failed")
            return False

    @commands.hybrid_command(name="gallery", description="Browse through all the puzzles you have started.")
    async def gallery(self, ctx: commands.Context):
        """Shows an interactive gallery of all puzzles — include puzzles with no collected pieces as well."""
        await ctx.defer(ephemeral=False)
        logger.info(f"[DEBUG] /gallery invoked by {ctx.author} ({ctx.author.id})")

        all_puzzles = list(self.bot.data.get("puzzles", {}).keys())
        all_puzzles.sort(key=lambda key: get_puzzle_display_name(self.bot.data, key))

        hidden = set(self.bot.data.get("hidden_puzzles", []))
        try:
            is_guild_context = ctx.guild is not None
            is_admin_user = False
            if is_guild_context:
                is_admin_user = ctx.author.id == getattr(ctx.guild, "owner_id", None) or ctx.author.guild_permissions.manage_guild
            privileged = set(self.bot.data.get("always_show_for", []))
            is_privileged_user = int(ctx.author.id) in {int(x) for x in privileged} if privileged else False
            if not (is_admin_user or is_privileged_user) and hidden:
                all_puzzles = [k for k in all_puzzles if k not in hidden]
        except Exception:
            logger.exception("Error while checking admin permissions for gallery filtering")

        all_puzzles.sort(key=lambda key: get_puzzle_display_name(self.bot.data, key))

        user_pieces = self.bot.data.get("user_pieces", {})
        user_puzzles = user_pieces.get(str(ctx.author.id), {})

        puzzles_with_pieces = [k for k in all_puzzles if k in user_puzzles and user_puzzles.get(k)]
        puzzles_without_pieces = [k for k in all_puzzles if k not in puzzles_with_pieces]
        user_puzzle_keys = puzzles_with_pieces + puzzles_without_pieces

        if not user_puzzle_keys:
            return await self._reply(ctx, "There are no puzzles configured yet.", ephemeral=True)

        interaction = getattr(ctx, "interaction", None)
        view = PuzzleGalleryView(self.bot, interaction, user_puzzle_keys, current_index=0, owner_id=ctx.author.id)
        embed, file = await view.generate_embed_and_file()

        await self._reply(ctx, None, embed=embed, file=file, view=view, ephemeral=False)

    @commands.hybrid_command(name="leaderboard", description="Show the top collectors for a puzzle.")
    @app_commands.autocomplete(puzzle_name=_puzzle_autocomplete)
    async def leaderboard(self, ctx: commands.Context, *, puzzle_name: str):
        await ctx.defer(ephemeral=False)
        puzzle_key = resolve_puzzle_key(self.bot.data, puzzle_name)
        if not puzzle_key:
            return await self._reply(ctx, f"{Emojis.FAILURE} Puzzle not found: `{puzzle_name}`", ephemeral=True)

        hidden = set(self.bot.data.get("hidden_puzzles", []))
        is_admin_user = False
        try:
            if ctx.guild is not None:
                is_admin_user = ctx.author.id == getattr(ctx.guild, "owner_id", None) or ctx.author.guild_permissions.manage_guild
        except Exception:
            is_admin_user = False

        privileged = set(self.bot.data.get("always_show_for", []))
        is_privileged_user = int(ctx.author.id) in {int(x) for x in privileged} if privileged else False

        if puzzle_key in hidden and not (is_admin_user or is_privileged_user):
            return await self._reply(ctx, f"{Emojis.FAILURE} Puzzle not found: `{puzzle_name}`", ephemeral=True)

        interaction = getattr(ctx, "interaction", None)
        if interaction:
            return await open_leaderboard_view(self.bot, interaction, puzzle_key)

        all_user_pieces = self.bot.data.get("user_pieces", {})
        leaderboard_data = [
            (int(user_id), len(user_puzzles.get(puzzle_key, [])))
            for user_id, user_puzzles in all_user_pieces.items()
            if puzzle_key in user_puzzles and len(user_puzzles[puzzle_key]) > 0
        ]
        leaderboard_data.sort(key=lambda x: (-x[1], x[0]))

        view = LeaderboardView(self.bot, ctx.guild, puzzle_key, leaderboard_data, page=0)
        embed = await view.generate_embed()
        await self._reply(ctx, None, embed=embed, view=view, ephemeral=False)

    @commands.hybrid_command(name="firstfinisher", description="Show who finished a puzzle first!")
    @app_commands.autocomplete(puzzle_name=_puzzle_autocomplete)
    async def firstfinisher(self, ctx: commands.Context, *, puzzle_name: str):
        puzzle_key = resolve_puzzle_key(self.bot.data, puzzle_name)
        finishers = self.bot.data.get("puzzle_finishers", {}).get(puzzle_key, [])
        if not finishers:
            return await self._reply(ctx, "No one has completed this puzzle yet!", ephemeral=True)
        first = finishers[0]
        user = self.bot.get_user(first["user_id"]) or await self.bot.fetch_user(first["user_id"])
        await self._reply(ctx, f"The first person to complete **{get_puzzle_display_name(self.bot.data, puzzle_key)}** was: {user.mention}!", ephemeral=False)

    @commands.hybrid_command(name="puzzle_toggle", description="Toggle hide/unhide state for a puzzle (admin only).")
    @app_commands.autocomplete(puzzle_name=_puzzle_autocomplete)
    @app_commands.choices(
        action=[
            app_commands.Choice(name="hide", value="hide"),
            app_commands.Choice(name="unhide", value="unhide"),
        ]
    )
    @is_admin()
    async def puzzle_toggle(self, ctx: commands.Context, puzzle_name: str, action: Optional[Literal["hide", "unhide"]] = None):
        await ctx.defer(ephemeral=True)
        puzzle_key = resolve_puzzle_key(self.bot.data, puzzle_name)
        if not puzzle_key:
            return await self._reply(ctx, f"❌ Category not found: `{puzzle_name}`", ephemeral=True)

        hidden = set(self.bot.data.get("hidden_puzzles", []))
        action_norm = (action or "").strip().lower()
        if action_norm not in ("hide", "unhide", ""):
            return await self._reply(ctx, "Invalid action. Use `hide`, `unhide`, or omit to toggle.", ephemeral=True)

        changed = False
        if action_norm == "hide":
            if puzzle_key in hidden:
                return await self._reply(ctx, f"ℹ️ Puzzle **{get_puzzle_display_name(self.bot.data, puzzle_key)}** is already hidden.", ephemeral=True)
            hidden.add(puzzle_key)
            changed = True
            result_msg = f"✅ Hidden puzzle **{get_puzzle_display_name(self.bot.data, puzzle_key)}**."
            action_taken = "hide"
        elif action_norm == "unhide":
            if puzzle_key not in hidden:
                return await self._reply(ctx, f"ℹ️ Puzzle **{get_puzzle_display_name(self.bot.data, puzzle_key)}** is not hidden.", ephemeral=True)
            hidden.remove(puzzle_key)
            changed = True
            result_msg = f"✅ Unhidden puzzle **{get_puzzle_display_name(self.bot.data, puzzle_key)}**."
            action_taken = "unhide"
        else:
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
            try:
                logger.info("puzzle_toggle: user=%s(%s) action=%s puzzle=%s", getattr(ctx.author, "name", None), getattr(ctx.author, "id", None), action_taken, puzzle_key)
            except Exception:
                logger.exception("puzzle_toggle: logger.info failed")

            audit_id = getattr(self.bot, "audit_log_channel_id", None) or getattr(self.bot, "audit_channel_id", None)
            if audit_id:
                try:
                    audit_ch = self.bot.get_channel(int(audit_id))
                    if audit_ch and isinstance(audit_ch, discord.abc.Messageable):
                        try:
                            asyncio.create_task(audit_ch.send(f"[Audit] Puzzle `{puzzle_key}` {action_taken}ed by {getattr(ctx.author, 'mention', str(getattr(ctx.author, 'id', 'unknown')))}"))
                        except Exception:
                            try:
                                await audit_ch.send(f"[Audit] Puzzle `{puzzle_key}` {action_taken}ed by {getattr(ctx.author, 'mention', str(getattr(ctx.author, 'id', 'unknown')))}")
                            except Exception:
                                logger.exception("puzzle_toggle: failed to send audit message")
                except Exception:
                    logger.exception("puzzle_toggle: failed to resolve audit channel %r", audit_id)

        await self._reply(ctx, result_msg, ephemeral=True)

    @commands.hybrid_command(name="puzzle_hidden_list", description="List puzzles currently hidden from member galleries.")
    @is_admin()
    async def puzzle_hidden_list(self, ctx: commands.Context):
        await ctx.defer(ephemeral=True)
        hidden = list(self.bot.data.get("hidden_puzzles", []))
        if not hidden:
            return await self._reply(ctx, "No puzzles are currently hidden.", ephemeral=True)
        lines = [f"- {get_puzzle_display_name(self.bot.data, key)} (`{key}`)" for key in hidden]
        try:
            logger.info("puzzle_hidden_list: user=%s(%s) listed %d hidden puzzles", getattr(ctx.author, "name", None), getattr(ctx.author, "id", None), len(hidden))
        except Exception:
            logger.exception("puzzle_hidden_list: logger.info failed")
        await self._reply(ctx, "Hidden puzzles:\n" + "\n".join(lines), ephemeral=True)

    @commands.hybrid_command(name="always_show_add", description="Allow a user to always see hidden puzzles (admin only).")
    @is_admin()
    async def always_show_add(self, ctx: commands.Context, user: discord.User):
        uid = int(user.id)
        self.bot.data.setdefault("always_show_for", [])
        if any(int(x) == uid for x in self.bot.data["always_show_for"]):
            return await self._reply(ctx, f"✅ {user} is already privileged to view hidden puzzles.", ephemeral=True)
        self.bot.data["always_show_for"].append(uid)
        try:
            save_data(self.bot.data)
        except Exception:
            logger.exception("always_show_add: persist failed")
        await self._reply(ctx, f"✅ {user} can now see hidden puzzles.", ephemeral=True)

    @commands.hybrid_command(name="always_show_remove", description="Remove a user from the always-show list (admin only).")
    @is_admin()
    async def always_show_remove(self, ctx: commands.Context, user: discord.User):
        uid = int(user.id)
        current = [int(x) for x in self.bot.data.get("always_show_for", [])]
        if uid not in current:
            return await self._reply(ctx, f"ℹ️ {user} is not in the always-show list.", ephemeral=True)
        self.bot.data["always_show_for"] = [x for x in current if int(x) != uid]
        try:
            save_data(self.bot.data)
        except Exception:
            logger.exception("always_show_remove: persist failed")
        await self._reply(ctx, f"✅ {user} no longer has privileged access to hidden puzzles.", ephemeral=True)

    @commands.hybrid_command(name="always_show_list", description="List users who can always view hidden puzzles (admin only).")
    @is_admin()
    async def always_show_list(self, ctx: commands.Context):
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

    def _collect_finish_events(self) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        pf = self.bot.data.get("puzzle_finishers", {}) or {}
        for puzzle_key, finishers in pf.items():
            if not isinstance(finishers, list):
                continue
            for pos, fin in enumerate(finishers, start=1):
                user_id = None
                ts = None
                try:
                    if isinstance(fin, dict):
                        user_id = int(fin.get("user_id"))
                        ts = fin.get("ts") or fin.get("timestamp") or fin.get("time")
                    else:
                        user_id = int(fin)
                except Exception:
                    continue
                events.append({"puzzle": puzzle_key, "user_id": user_id, "position": pos, "ts": ts})
        return events

    @commands.hybrid_command(name="finishes_log", description="Export a log of finishers (first-finish list) across all puzzles.")
    @is_admin()
    async def finishes_log(self, ctx: commands.Context, chronological: Optional[bool] = True):
        await ctx.defer(ephemeral=True)
        events = self._collect_finish_events()
        has_ts = any(e.get("ts") for e in events)
        out_lines = []
        header = "timestamp,puzzle_key,puzzle_name,position,user_id,user_display\n"
        out_lines.append(header)

        if chronological and has_ts:
            def _key(ev):
                t = ev.get("ts")
                if not t:
                    return datetime.max.replace(tzinfo=timezone.utc)
                try:
                    if isinstance(t, (int, float)):
                        return datetime.fromtimestamp(float(t), tz=timezone.utc)
                    return datetime.fromisoformat(str(t)).astimezone(timezone.utc)
                except Exception:
                    return datetime.max.replace(tzinfo=timezone.utc)
            events_sorted = sorted(events, key=_key)
        else:
            events_sorted = sorted(events, key=lambda e: (e["puzzle"], e["position"]))

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
        await ctx.defer(ephemeral=False)
        pf = self.bot.data.get("puzzle_finishers", {}) or {}
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
                if uid in seen_for_puzzle:
                    continue
                seen_for_puzzle.add(uid)
                user_puzzles.setdefault(uid, set()).add(puzzle_key)

        leaderboard = [(uid, len(puzzles)) for uid, puzzles in user_puzzles.items()]
        leaderboard.sort(key=lambda x: (-x[1], x[0]))

        lines = ["Overall finishes leaderboard (number of puzzles finished):\n"]
        if not leaderboard:
            lines.append("No finishes recorded.\n")
        else:
            for rank, (uid, cnt) in enumerate(leaderboard, start=1):
                try:
                    user = self.bot.get_user(int(uid)) or await self.bot.fetch_user(int(uid))
                    mention = user.mention
                except Exception:
                    mention = f"`{uid}`"
                lines.append(f"{rank}. {mention} — {cnt} puzzles\n")

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
        await ctx.defer(ephemeral=True)
        pf = self.bot.data.get("puzzle_finishers", {}) or {}
        to_update = []
        now = datetime.now(timezone.utc)
        delta_seconds = 0
        for puzzle_key, finishers in pf.items():
            if not isinstance(finishers, list):
                continue
            for pos, fin in enumerate(finishers, start=1):
                if isinstance(fin, dict) and fin.get("ts"):
                    continue
                suggested_ts = (now + timedelta(seconds=delta_seconds)).isoformat()
                to_update.append((puzzle_key, pos - 1, suggested_ts))
                delta_seconds += 1

        if not to_update:
            return await self._reply(ctx, "No missing timestamps found; nothing to backfill.", ephemeral=True)

        if not apply:
            return await self._reply(ctx, f"Dry-run: {len(to_update)} finish records would be backfilled. Re-run with apply=True to commit.", ephemeral=True)

        for puzzle_key, idx, ts in to_update:
            try:
                fin = self.bot.data["puzzle_finishers"][puzzle_key][idx]
                if isinstance(fin, dict):
                    fin["ts"] = ts
                else:
                    self.bot.data["puzzle_finishers"][puzzle_key][idx] = {"user_id": int(fin), "ts": ts}
            except Exception:
                logger.exception("Failed to backfill finish record %s[%s]", puzzle_key, idx)

        try:
            save_data(self.bot.data)
        except Exception:
            logger.exception("Failed to persist after finishes_backfill_ts")

        await self._reply(ctx, f"Backfilled {len(to_update)} finish records with timestamps.", ephemeral=True)

    @commands.hybrid_command(name="finishes_by_puzzle", description="List finishers per puzzle (sequential order).")
    @is_admin()
    async def finishes_by_puzzle(self, ctx: commands.Context, *, include_empty: bool = False):
        await ctx.defer(ephemeral=True)
        puzzles_meta = self.bot.data.get("puzzles", {}) or {}
        finishers_map = self.bot.data.get("puzzle_finishers", {}) or {}

        lines: List[str] = []
        puzzle_keys = list(puzzles_meta.keys()) or sorted(finishers_map.keys())

        for pkey in puzzle_keys:
            display = (puzzles_meta.get(pkey) or {}).get("display_name") or pkey
            fins = finishers_map.get(pkey, []) or []
            if not fins and not include_empty:
                continue
            lines.append(display)
            if not fins:
                lines.append("  (no finishers recorded)")
                lines.append("")
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
            lines.append("")

        if not lines:
            return await self._reply(ctx, "No finishers recorded.", ephemeral=True)

        out = "\n".join(lines)
        if len(out) > 1900:
            bio = io.BytesIO(out.encode("utf-8"))
            bio.seek(0)
            await self._reply(ctx, "Finishes by puzzle (file):", file=discord.File(bio, filename="finishes_by_puzzle.txt"), ephemeral=True)
        else:
            await self._reply(ctx, f"```\n{out}\n```", ephemeral=True)

    @commands.hybrid_command(name="remove_finisher", description="Remove a user's finish record for a puzzle (admin only).")
    @app_commands.autocomplete(puzzle_name=_puzzle_autocomplete)
    @is_admin()
    async def remove_finisher(self, ctx: commands.Context, puzzle_name: str, user: discord.User, position: Optional[int] = None):
        await ctx.defer(ephemeral=True)
        puzzle_key = resolve_puzzle_key(self.bot.data, puzzle_name)
        if not puzzle_key:
            return await self._reply(ctx, f"❌ Puzzle not found: `{puzzle_name}`", ephemeral=True)

        pf = self.bot.data.setdefault("puzzle_finishers", {})
        finishers = pf.get(puzzle_key, []) or []
        if not finishers:
            return await self._reply(ctx, f"ℹ️ No finishers recorded for puzzle `{puzzle_key}`.", ephemeral=True)

        target_uid = int(user.id)
        removed_count = 0
        new_finishers: List[Any] = []

        if position is not None:
            pos_idx = position - 1
            if pos_idx < 0 or pos_idx >= len(finishers):
                return await self._reply(ctx, f"❌ Position {position} is out of range for puzzle `{puzzle_key}` (1..{len(finishers)}).", ephemeral=True)
            fin = finishers[pos_idx]
            try:
                fin_uid = int(fin.get("user_id")) if isinstance(fin, dict) else int(fin)
            except Exception:
                fin_uid = None
            if fin_uid != target_uid:
                return await self._reply(ctx, f"❌ Position {position} for `{puzzle_key}` is not {user.mention}. It belongs to `{fin_uid}`.", ephemeral=True)
            new_finishers = finishers[:pos_idx] + finishers[pos_idx + 1:]
            removed_count = 1
        else:
            for fin in finishers:
                try:
                    fin_uid = int(fin.get("user_id")) if isinstance(fin, dict) else int(fin)
                except Exception:
                    new_finishers.append(fin)
                    continue
                if fin_uid == target_uid:
                    removed_count += 1
                else:
                    new_finishers.append(fin)

        if removed_count == 0:
            return await self._reply(ctx, f"ℹ️ No finish entries found for {user.mention} on puzzle `{puzzle_key}`.", ephemeral=True)

        self.bot.data["puzzle_finishers"][puzzle_key] = new_finishers
        try:
            save_data(self.bot.data)
        except Exception:
            logger.exception("remove_finisher: failed to persist puzzle_finishers")

        try:
            logger.info("remove_finisher: user=%s(%s) removed=%d puzzle=%s by=%s(%s)", getattr(user, "name", None), target_uid, removed_count, puzzle_key, getattr(ctx.author, "name", None), getattr(ctx.author, "id", None))
        except Exception:
            logger.exception("remove_finisher: logger.info failed")

        audit_id = getattr(self.bot, "audit_log_channel_id", None) or getattr(self.bot, "audit_channel_id", None)
        if audit_id:
            try:
                ch = self.bot.get_channel(int(audit_id))
                if ch and isinstance(ch, discord.abc.Messageable):
                    try:
                        asyncio.create_task(ch.send(f"[Audit] Removed {removed_count} finisher(s) for puzzle `{puzzle_key}` for {user.mention} (by {ctx.author.mention})"))
                    except Exception:
                        try:
                            await ch.send(f"[Audit] Removed {removed_count} finisher(s) for puzzle `{puzzle_key}` for {user.mention} (by {ctx.author.mention})")
                        except Exception:
                            logger.exception("remove_finisher: failed to send audit message")
            except Exception:
                logger.exception("remove_finisher: failed to resolve audit channel %r", audit_id)

        await self._reply(ctx, f"✅ Removed {removed_count} finisher(s) for {user.mention} on puzzle **{get_puzzle_display_name(self.bot.data, puzzle_key)}**.", ephemeral=True)

    @commands.hybrid_command(name="clear_finishers", description="Clear all finishers for a puzzle (admin only).")
    @app_commands.autocomplete(puzzle_name=_puzzle_autocomplete)
    @is_admin()
    async def clear_finishers(self, ctx: commands.Context, puzzle_name: str, apply: Optional[bool] = False):
        await ctx.defer(ephemeral=True)
        puzzle_key = resolve_puzzle_key(self.bot.data, puzzle_name)
        if not puzzle_key:
            return await self._reply(ctx, f"❌ Puzzle not found: `{puzzle_name}`", ephemeral=True)

        pf = self.bot.data.get("puzzle_finishers", {}) or {}
        finishers = pf.get(puzzle_key, []) or []
        count = len(finishers)
        if count == 0:
            return await self._reply(ctx, f"ℹ️ No finishers recorded for puzzle `{puzzle_key}`.", ephemeral=True)

        if not apply:
            return await self._reply(ctx, f"⚠️ Dry-run: {count} finisher(s) would be removed for puzzle **{get_puzzle_display_name(self.bot.data, puzzle_key)}**. Re-run with apply=True to commit.", ephemeral=True)

        confirm = await self._confirm(ctx, f"Are you sure you want to CLEAR {count} finisher(s) for **{get_puzzle_display_name(self.bot.data, puzzle_key)}**? This cannot be undone.")
        if not confirm:
            return await self._reply(ctx, "Cancelled — no changes made.", ephemeral=True)

        self.bot.data.setdefault("puzzle_finishers", {})[puzzle_key] = []
        try:
            save_data(self.bot.data)
        except Exception:
            logger.exception("clear_finishers: failed to persist puzzle_finishers")

        try:
            logger.info("clear_finishers: cleared %d finishers for puzzle=%s by=%s(%s)", count, puzzle_key, getattr(ctx.author, "name", None), getattr(ctx.author, "id", None))
        except Exception:
            logger.exception("clear_finishers: logger.info failed")

        audit_id = getattr(self.bot, "audit_log_channel_id", None) or getattr(self.bot, "audit_channel_id", None)
        if audit_id:
            try:
                ch = self.bot.get_channel(int(audit_id))
                if ch and isinstance(ch, discord.abc.Messageable):
                    try:
                        asyncio.create_task(ch.send(f"[Audit] Cleared {count} finisher(s) for puzzle `{puzzle_key}` (by {ctx.author.mention})"))
                    except Exception:
                        try:
                            await ch.send(f"[Audit] Cleared {count} finisher(s) for puzzle `{puzzle_key}` (by {ctx.author.mention})")
                        except Exception:
                            logger.exception("clear_finishers: failed to send audit message")
            except Exception:
                logger.exception("clear_finishers: failed to resolve audit channel %r", audit_id)

        await self._reply(ctx, f"✅ Cleared {count} finisher(s) for puzzle **{get_puzzle_display_name(self.bot.data, puzzle_key)}**.", ephemeral=True)

    @commands.hybrid_command(name="remove_user_finishes", description="Remove a user's finisher entries across all puzzles (admin only).")
    @is_admin()
    async def remove_user_finishes(self, ctx: commands.Context, user: discord.User, apply: Optional[bool] = False):
        await ctx.defer(ephemeral=True)
        uid = int(user.id)
        pf = self.bot.data.get("puzzle_finishers", {}) or {}

        total_found = 0
        per_puzzle_counts: Dict[str, int] = {}
        for pkey, finishers in list(pf.items()):
            if not isinstance(finishers, list):
                continue
            removed = 0
            for fin in finishers:
                try:
                    fin_uid = int(fin.get("user_id")) if isinstance(fin, dict) else int(fin)
                except Exception:
                    continue
                if fin_uid == uid:
                    removed += 1
            if removed:
                per_puzzle_counts[pkey] = removed
                total_found += removed

        if total_found == 0:
            return await self._reply(ctx, f"ℹ️ No finish entries found for {user.mention}.", ephemeral=True)

        if not apply:
            lines = [f"- {get_puzzle_display_name(self.bot.data, p)} (`{p}`): {c} entry(ies)" for p, c in per_puzzle_counts.items()]
            return await self._reply(ctx, f"⚠️ Dry-run: would remove {total_found} finisher entry(ies) for {user.mention}:\n" + "\n".join(lines) + "\nRe-run with apply=True to commit.", ephemeral=True)

        confirm = await self._confirm(ctx, f"Are you sure you want to REMOVE {total_found} finisher entry(ies) for {user.mention} across {len(per_puzzle_counts)} puzzle(s)?")
        if not confirm:
            return await self._reply(ctx, "Cancelled — no changes made.", ephemeral=True)

        for pkey in per_puzzle_counts.keys():
            finishers = pf.get(pkey, []) or []
            new_list = []
            for fin in finishers:
                try:
                    fin_uid = int(fin.get("user_id")) if isinstance(fin, dict) else int(fin)
                except Exception:
                    new_list.append(fin)
                    continue
                if fin_uid != uid:
                    new_list.append(fin)
            self.bot.data["puzzle_finishers"][pkey] = new_list

        try:
            save_data(self.bot.data)
        except Exception:
            logger.exception("remove_user_finishes: failed to persist puzzle_finishers")

        try:
            logger.info("remove_user_finishes: removed %d entries for user=%s(%s) by=%s(%s)", total_found, getattr(user, "name", None), uid, getattr(ctx.author, "name", None), getattr(ctx.author, "id", None))
        except Exception:
            logger.exception("remove_user_finishes: logger.info failed")

        audit_id = getattr(self.bot, "audit_log_channel_id", None) or getattr(self.bot, "audit_channel_id", None)
        if audit_id:
            try:
                ch = self.bot.get_channel(int(audit_id))
                if ch and isinstance(ch, discord.abc.Messageable):
                    try:
                        asyncio.create_task(ch.send(f"[Audit] Removed {total_found} finisher(s) for {user.mention} across {len(per_puzzle_counts)} puzzle(s) (by {ctx.author.mention})"))
                    except Exception:
                        try:
                            await ch.send(f"[Audit] Removed {total_found} finisher(s) for {user.mention} across {len(per_puzzle_counts)} puzzle(s) (by {ctx.author.mention})")
                        except Exception:
                            logger.exception("remove_user_finishes: failed to send audit message")
            except Exception:
                logger.exception("remove_user_finishes: failed to resolve audit channel %r", audit_id)

        await self._reply(ctx, f"✅ Removed {total_found} finisher entry(ies) for {user.mention}.", ephemeral=True)

    @commands.hybrid_command(name="wipe_all_finishers", description="Wipe all finisher data for all puzzles (admin only).")
    @is_admin()
    async def wipe_all_finishers(self, ctx: commands.Context, apply: Optional[bool] = False):
        await ctx.defer(ephemeral=True)
        pf = self.bot.data.get("puzzle_finishers", {}) or {}
        total = sum(len(f) for f in pf.values() if isinstance(f, list))

        if total == 0:
            return await self._reply(ctx, "ℹ️ No finisher records exist.", ephemeral=True)

        if not apply:
            return await self._reply(ctx, f"⚠️ Dry-run: {total} finisher entry(ies) across {len(pf)} puzzle(s) would be wiped. Re-run with apply=True to commit.", ephemeral=True)

        confirm = await self._confirm(ctx, f"ARE YOU SURE? This will WIPE {total} finisher entry(ies) across {len(pf)} puzzle(s). This cannot be undone.")
        if not confirm:
            return await self._reply(ctx, "Cancelled — no changes made.", ephemeral=True)

        for pkey in list(pf.keys()):
            self.bot.data["puzzle_finishers"][pkey] = []

        try:
            save_data(self.bot.data)
        except Exception:
            logger.exception("wipe_all_finishers: failed to persist puzzle_finishers")

        try:
            logger.info("wipe_all_finishers: wiped %d finisher entries across %d puzzles by=%s(%s)", total, len(pf), getattr(ctx.author, "name", None), getattr(ctx.author, "id", None))
        except Exception:
            logger.exception("wipe_all_finishers: logger.info failed")

        audit_id = getattr(self.bot, "audit_log_channel_id", None) or getattr(self.bot, "audit_channel_id", None)
        if audit_id:
            try:
                ch = self.bot.get_channel(int(audit_id))
                if ch and isinstance(ch, discord.abc.Messageable):
                    try:
                        asyncio.create_task(ch.send(f"[Audit] Wiped {total} finisher(s) across {len(pf)} puzzles (by {ctx.author.mention})"))
                    except Exception:
                        try:
                            await ch.send(f"[Audit] Wiped {total} finisher(s) across {len(pf)} puzzles (by {ctx.author.mention})")
                        except Exception:
                            logger.exception("wipe_all_finishers: failed to send audit message")
            except Exception:
                logger.exception("wipe_all_finishers: failed to resolve audit channel %r", audit_id)

        await self._reply(ctx, f"✅ Wiped {total} finisher entry(ies) across {len(pf)} puzzle(s).", ephemeral=True)

    @commands.hybrid_command(name="giveitem", description="Give a snowman part or a puzzle piece to a user.")
    @is_admin()
    async def giveitem(self, ctx: commands.Context, member: discord.Member, required: Optional[bool] = False, item_type: str = None, key: str = None, spec: str = None):
        await ctx.defer(ephemeral=True)

        if not item_type or not key or not spec:
            return await self._reply(ctx, "Usage: /giveitem @user <required:bool> <snowman|puzzle> <buildable_or_puzzle_key> <part_or_piece_id>", ephemeral=True)

        itype = item_type.strip().lower()
        if itype in ("snowman", "stocking", "buildable"):
            stocking_cog = self.bot.get_cog("StockingCog")
            if not stocking_cog:
                return await self._reply(ctx, "Stocking cog is not available on this bot (cannot give snowman parts).", ephemeral=True)

            buildable_key = key.strip()
            part_key = spec.strip()

            try:
                buildables_def = getattr(stocking_cog, "_buildables_def", {}) or {}
                if buildables_def and buildable_key not in buildables_def:
                    return await self._reply(ctx, f"Unknown buildable '{buildable_key}'. Valid keys: {', '.join(sorted(buildables_def.keys()))}", ephemeral=True)
                if buildables_def and part_key not in (buildables_def.get(buildable_key, {}).get("parts") or {}):
                    return await self._reply(ctx, f"Unknown part '{part_key}' for buildable '{buildable_key}'. Valid parts: {', '.join(sorted((buildables_def.get(buildable_key,{}).get('parts') or {}).keys()))}", ephemeral=True)
            except Exception:
                pass

            try:
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

        elif itype in ("puzzle", "piece", "puzz"):
            puzzle_key_raw = key.strip()
            piece_id = spec.strip()
            resolved = resolve_puzzle_key(self.bot.data, puzzle_key_raw)
            if not resolved:
                return await self._reply(ctx, f"Puzzle not found: `{puzzle_key_raw}`", ephemeral=True)
            puzzle_key = resolved

            pieces_map = (self.bot.data.get("pieces", {}) or {}).get(puzzle_key, {}) or {}
            piece_exists = False
            try:
                if pieces_map and piece_id in pieces_map:
                    piece_exists = True
                else:
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

            try:
                save_data(self.bot.data)
            except Exception:
                logger.exception("giveitem: failed to persist data after add_piece_to_user")

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
                pass

            return await self._reply(ctx, f"✅ Granted piece `{piece_id}` for puzzle `{puzzle_key}` to {member.mention}.", ephemeral=True)
        else:
            return await self._reply(ctx, "Unknown item_type — expected 'snowman' or 'puzzle'.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(PuzzlesCog(bot))