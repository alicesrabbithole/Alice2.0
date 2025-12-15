import discord
from discord.ext import commands, tasks
from discord import app_commands
import random
import io
import logging
from typing import Optional, List
from datetime import datetime, timezone, timedelta
from PIL import Image

import config
from utils.db_utils import (
    save_data,
    resolve_puzzle_key,
    get_puzzle_display_name,
)
from utils.log_utils import log
from ui.views import DropView
from utils.checks import is_admin
from utils.theme import THEMES, PUZZLE_CONFIG, Emojis, Colors  # Make sure Emojis and Colors are imported

logger = logging.getLogger(__name__)

# Frequency levels for the "random/frequency" mode.
# Each level defines a range for time (seconds) and messages (count).
FREQUENCY_LEVELS = {
    "slow": {"time": (90 * 60, 120 * 60), "messages": (150, 200)},
    "average": {"time": (60 * 60, 90 * 60), "messages": (100, 150)},
    "fast": {"time": (30 * 60, 60 * 60), "messages": (50, 100)},
}


class PuzzleDropsCog(commands.Cog, name="Puzzle Drops"):
    """Manages the automatic and manual dropping of puzzle pieces."""

    DEFAULT_DROP_MODE = "timer"
    DEFAULT_DROP_TIMER_MINUTES = 45
    DEFAULT_DROP_MESSAGE_COUNT = 100

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.drop_scheduler.start()

    def cog_unload(self):
        self.drop_scheduler.cancel()

    async def puzzle_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        """Autocomplete puzzle slugs/display names (same behavior as before)."""
        choices = []
        try:
            puzzles = self.bot.data.get("puzzles")
            if not isinstance(puzzles, dict):
                logger.warning("Autocomplete failed: self.bot.data['puzzles'] is not a dict or is missing.")
                return []
            if "all puzzles".startswith(current.lower()):
                choices.append(app_commands.Choice(name="All Puzzles (Random)", value="all_puzzles"))
            for slug, meta in puzzles.items():
                if not isinstance(slug, str) or not isinstance(meta, dict):
                    continue
                display_name = meta.get("display_name", slug.replace("_", " ").title())
                if current.lower() in slug.lower() or current.lower() in display_name.lower():
                    choices.append(app_commands.Choice(name=display_name, value=slug))
        except Exception as e:
            logger.exception("Unhandled exception in puzzle_autocomplete: %s", e)
            return []
        return choices[:25]

    async def mode_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        """Autocomplete mode choices."""
        choices = []
        for m in ("timer", "messages", "random"):
            if m.startswith(current.lower()):
                choices.append(app_commands.Choice(name=m, value=m))
        return choices[:10]

    async def speed_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        """Autocomplete frequency level (slow/average/fast) for random mode."""
        choices = []
        for lvl in ("slow", "average", "fast"):
            if lvl.startswith(current.lower()):
                pretty = f"{lvl} (messages {FREQUENCY_LEVELS[lvl]['messages'][0]}-{FREQUENCY_LEVELS[lvl]['messages'][1]}, time {FREQUENCY_LEVELS[lvl]['time'][0]//60}-{FREQUENCY_LEVELS[lvl]['time'][1]//60} min)"
                choices.append(app_commands.Choice(name=pretty, value=lvl))
        return choices[:10]

    def _parse_range_value(self, value: Optional[str], *, as_minutes: bool = False) -> Optional[int]:
        """
        Parse a value that can be:
         - None
         - an exact integer string: "30"
         - a range "25-30" -> returns a random int in that inclusive range
        If as_minutes=True, returned integer is in seconds (minutes -> seconds)
        """
        if value is None:
            return None
        try:
            if isinstance(value, int):
                val = int(value)
                return val * 60 if as_minutes else val
            s = str(value).strip()
            if "-" in s:
                parts = s.split("-", 1)
                lo = int(parts[0].strip())
                hi = int(parts[1].strip())
                chosen = random.randint(min(lo, hi), max(lo, hi))
                return chosen * 60 if as_minutes else chosen
            # single number
            val = int(s)
            return val * 60 if as_minutes else val
        except Exception:
            return None

    def _available_puzzles(self) -> List[str]:
        """
        Return list of puzzle keys that should be considered for automatic drops.
        Excludes puzzles listed in self.bot.data['hidden_puzzles'].
        """
        puzzles = self.bot.data.get("puzzles") or {}
        if not isinstance(puzzles, dict):
            return []
        hidden = set(self.bot.data.get("hidden_puzzles", []) or [])
        return [k for k in puzzles.keys() if k not in hidden]

    async def _spawn_drop(
        self, channel: discord.TextChannel, puzzle_key: str, forced_piece: Optional[str] = None
    ):
        # Pull config and theme info
        meta = PUZZLE_CONFIG.get(puzzle_key, {})
        theme_name = meta.get("theme")
        theme = THEMES.get(theme_name) if theme_name else None

        display_name = meta.get("display_name", get_puzzle_display_name(self.bot.data, puzzle_key))
        pieces_map = self.bot.data.get("pieces", {}).get(puzzle_key)
        if not pieces_map:
            logger.warning(f"No pieces found for puzzle {puzzle_key}")
            return

        piece_id = forced_piece or random.choice(list(pieces_map.keys()))
        piece_path = pieces_map.get(piece_id)
        if not piece_path:
            logger.warning(f"Piece {piece_id} not found in map for puzzle {puzzle_key}")
            return

        full_path = config.PUZZLES_ROOT / piece_path
        logger.debug(f"Attempting to open piece {piece_id} at {full_path}")

        try:
            with Image.open(full_path) as img:
                img.thumbnail((128, 128))
                buffer = io.BytesIO()
                img.save(buffer, "PNG")
                buffer.seek(0)
                file = discord.File(buffer, filename="puzzle_piece.png")
        except Exception as e:
            logger.exception(f"Failed to process image for drop, using raw file: {e}")
            file = discord.File(full_path, filename="puzzle_piece.png")

        ping_role_id = self.bot.data.get("piece_drop_ping_role_id")
        ping_out = f"<@&{ping_role_id}>" if ping_role_id else None

        emoji = theme.emoji if theme else (getattr(config, "CUSTOM_EMOJI_STRING", Emojis.PUZZLE_PIECE))
        embed_color = theme.color if theme else Colors.THEME_COLOR
        button_color = theme.button_color if theme else Colors.THEME_COLOR

        embed = (
            discord.Embed(
                title=f"{emoji} A Wild Puzzle Piece Appears!",
                description=f"A piece of the **{display_name}** puzzle has dropped!\nClick the button to collect it.",
                color=embed_color,
            )
            .set_image(url="attachment://puzzle_piece.png")
        )

        raw_cfg = self.bot.data.get("drop_channels", {}).get(str(channel.id), {})
        claims_range = raw_cfg.get("claims_range", [1, 3])
        claim_limit = random.randint(claims_range[0], claims_range[1])
        view = DropView(self.bot, puzzle_key, display_name, piece_id, claim_limit, button_color=button_color)

        try:
            message = await channel.send(
                content=ping_out,
                embed=embed,
                file=file,
                view=view
            )
            view.message = message
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.exception(f"Failed to send drop in #{channel.name}: {e}")

    @tasks.loop(seconds=30)
    async def drop_scheduler(self):
        await self.bot.wait_until_ready()
        now = datetime.now(timezone.utc)
        drop_channels = self.bot.data.get("drop_channels", {})
        data_changed = False

        for ch_id_str, raw_cfg in drop_channels.items():
            mode = raw_cfg.get("mode")
            channel = self.bot.get_channel(int(ch_id_str))
            if not channel:
                continue

            # --- Frequency random mode (uses FREQUENCY_LEVELS) ---
            if mode == "random" and raw_cfg.get("trigger") == "frequency":
                freq_level = raw_cfg.get("frequency_level", "average")
                ranges = FREQUENCY_LEVELS.get(freq_level, FREQUENCY_LEVELS["average"])
                min_secs, max_secs = ranges["time"]
                min_msgs, max_msgs = ranges["messages"]

                # Ensure targets initialized
                if "next_trigger_time" not in raw_cfg:
                    raw_cfg["next_trigger_time"] = random.randint(min_secs, max_secs)
                    data_changed = True
                if "next_trigger_messages" not in raw_cfg:
                    raw_cfg["next_trigger_messages"] = random.randint(min_msgs, max_msgs)
                    data_changed = True
                if "message_count" not in raw_cfg:
                    raw_cfg["message_count"] = 0
                    data_changed = True

                last_drop_str = raw_cfg.get("last_drop_time")
                if not last_drop_str:
                    raw_cfg["last_drop_time"] = now.isoformat()
                    data_changed = True
                    continue

                last_drop_time = datetime.fromisoformat(last_drop_str)
                time_ready = now >= last_drop_time + timedelta(seconds=raw_cfg["next_trigger_time"])
                msgs_reached = raw_cfg.get("message_count", 0) >= raw_cfg.get("next_trigger_messages", min_msgs)

                # If either condition is met, trigger. Update state *before* calling _spawn_drop to avoid races.
                if time_ready or msgs_reached:
                    # choose from non-hidden puzzles only
                    candidates = self._available_puzzles()
                    if not candidates:
                        logger.debug("drop_scheduler: no available (non-hidden) puzzles to spawn for random frequency mode; skipping.")
                        continue
                    puzzle_key = random.choice(candidates)
                    # Update timing state up-front
                    raw_cfg["last_drop_time"] = now.isoformat()
                    raw_cfg["next_trigger_time"] = random.randint(min_secs, max_secs)
                    raw_cfg["next_trigger_messages"] = random.randint(min_msgs, max_msgs)
                    raw_cfg["message_count"] = 0
                    data_changed = True

                    await self._spawn_drop(channel, puzzle_key)
                continue

            # --- Timer mode ---
            elif mode == "timer":
                last_drop_str = raw_cfg.get("last_drop_time")
                if not last_drop_str:
                    raw_cfg["last_drop_time"] = now.isoformat()
                    data_changed = True
                    continue
                last_drop_time = datetime.fromisoformat(last_drop_str)
                seconds_to_wait = raw_cfg.get("value", 3600)
                if now >= last_drop_time + timedelta(seconds=seconds_to_wait):
                    puzzle_slug = raw_cfg.get("puzzle")
                    if not puzzle_slug:
                        continue
                    if puzzle_slug == "all_puzzles":
                        # choose from non-hidden puzzles only
                        candidates = self._available_puzzles()
                        puzzle_key = random.choice(candidates) if candidates else None
                    else:
                        puzzle_key = resolve_puzzle_key(self.bot.data, puzzle_slug)
                        # if the chosen puzzle is hidden, skip the spawn
                        if puzzle_key and puzzle_key in set(self.bot.data.get("hidden_puzzles", []) or []):
                            logger.debug("drop_scheduler: timer mode would spawn hidden puzzle %s; skipping.", puzzle_key)
                            puzzle_key = None
                    if puzzle_key:
                        await self._spawn_drop(channel, puzzle_key)
                        raw_cfg["last_drop_time"] = now.isoformat()
                        data_changed = True

            # --- Messages mode ---
            elif mode == "messages":
                # handled in on_message for message triggers; keep here for safety/backwards compat
                continue

        if data_changed:
            save_data(self.bot.data)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        raw_cfg = self.bot.data.get("drop_channels", {}).get(str(message.channel.id))
        if not raw_cfg:
            return

        # --- Frequency random mode (message-based) ---
        if raw_cfg.get("mode") == "random" and raw_cfg.get("trigger") == "frequency":
            freq_level = raw_cfg.get("frequency_level", "average")
            ranges = FREQUENCY_LEVELS.get(freq_level, FREQUENCY_LEVELS["average"])
            min_secs, max_secs = ranges["time"]
            min_msgs, max_msgs = ranges["messages"]

            # increment message counter
            raw_cfg["message_count"] = raw_cfg.get("message_count", 0) + 1

            next_msgs = raw_cfg.get("next_trigger_messages", random.randint(min_msgs, max_msgs))
            now = datetime.now(timezone.utc)
            last_drop_str = raw_cfg.get("last_drop_time")
            if not last_drop_str:
                raw_cfg["last_drop_time"] = now.isoformat()
                # don't spawn on the first message
                save_data(self.bot.data)
                return

            last_drop_time = datetime.fromisoformat(last_drop_str)
            time_ready = now >= last_drop_time + timedelta(seconds=raw_cfg.get("next_trigger_time", random.randint(min_secs, max_secs)))
            msgs_reached = raw_cfg["message_count"] >= next_msgs

            # If either condition met, trigger; update state before spawn to avoid duplicates.
            if msgs_reached or time_ready:
                # choose from non-hidden puzzles only
                candidates = self._available_puzzles()
                if candidates:
                    puzzle_key = random.choice(candidates)
                    raw_cfg["last_drop_time"] = now.isoformat()
                    raw_cfg["next_trigger_time"] = random.randint(min_secs, max_secs)
                    raw_cfg["next_trigger_messages"] = random.randint(min_msgs, max_msgs)
                    raw_cfg["message_count"] = 0
                    save_data(self.bot.data)
                    await self._spawn_drop(message.channel, puzzle_key)
                else:
                    logger.debug("on_message: no available (non-hidden) puzzles to spawn for random frequency mode; skipping.")
            return

        # --- Old messages mode ---
        if raw_cfg.get("mode") == "messages":
            raw_cfg["message_count"] = raw_cfg.get("message_count", 0) + 1
            if raw_cfg["message_count"] >= raw_cfg.get("next_trigger", raw_cfg.get("value")):
                puzzle_slug = raw_cfg.get("puzzle")
                if puzzle_slug == "all_puzzles":
                    # choose from non-hidden puzzles only
                    candidates = self._available_puzzles()
                    puzzle_key = random.choice(candidates) if candidates else None
                else:
                    puzzle_key = resolve_puzzle_key(self.bot.data, puzzle_slug)
                    # skip if hidden
                    if puzzle_key and puzzle_key in set(self.bot.data.get("hidden_puzzles", []) or []):
                        logger.debug("on_message: messages mode would spawn hidden puzzle %s; skipping.", puzzle_key)
                        puzzle_key = None
                if puzzle_key:
                    await self._spawn_drop(message.channel, puzzle_key)
                    raw_cfg["message_count"] = 0
                save_data(self.bot.data)

    @commands.hybrid_command(
        name="spawndrop",
        description="Manually spawn a puzzle drop (optionally for a specific piece)."
    )
    @app_commands.autocomplete(puzzle=puzzle_autocomplete)
    @is_admin()
    async def spawndrop(
        self,
        ctx: commands.Context,
        puzzle: str,
        channel: Optional[discord.TextChannel] = None,
        piece: Optional[str] = None
    ):
        await ctx.defer(ephemeral=True)
        target_channel = channel or ctx.channel
        if puzzle == "all_puzzles":
            all_puzzles = list(self.bot.data.get("puzzles", {}).keys())
            if not all_puzzles:
                return await ctx.send("❌ No puzzles are available.", ephemeral=True)
            puzzle_key = random.choice(all_puzzles)
        else:
            puzzle_key = resolve_puzzle_key(self.bot.data, puzzle)
        if not puzzle_key:
            return await ctx.send(f"❌ Puzzle not found: `{puzzle}`", ephemeral=True)

        piece_id = None
        if piece is not None:
            pieces_map = self.bot.data.get("pieces", {}).get(puzzle_key)
            if not pieces_map or piece not in pieces_map:
                return await ctx.send(f"❌ Piece `{piece}` is not valid for puzzle `{puzzle}`.", ephemeral=True)
            piece_id = piece

        await self._spawn_drop(target_channel, puzzle_key, forced_piece=piece_id)
        display_name = get_puzzle_display_name(self.bot.data, puzzle_key)
        await ctx.send(
            f"✅ Drop for **{display_name}** spawned in {target_channel.mention}.",
            ephemeral=True
        )

    @commands.hybrid_command(
        name="pingset_drops",
        description="Set the role to ping on every puzzle piece drop."
    )
    @is_admin()
    async def pingset_drops(self, ctx: commands.Context, role: discord.Role):
        self.bot.data["piece_drop_ping_role_id"] = role.id
        save_data(self.bot.data)
        await ctx.send(f"🛎️ Drop ping role has been set to {role.mention}. Future drops will ping this role.", ephemeral=False)

    @commands.hybrid_command(
        name="setdropchannel",
        description="Configure a channel for automatic puzzle drops."
    )
    @app_commands.autocomplete(puzzle=puzzle_autocomplete, mode=mode_autocomplete, frequency_level=speed_autocomplete)
    @is_admin()
    async def setdropchannel(
            self,
            ctx: commands.Context,
            channel: discord.TextChannel,
            puzzle: str,
            mode: Optional[str] = None,
            value: Optional[str] = None,
            frequency_level: Optional[str] = None
    ):
        """
        Configure a channel for automatic drops.

        - mode: "timer", "messages", or "random" (random uses frequency-based triggers)
        - value: single number or range "min-max". For timer, value is minutes.
        - frequency_level (for random): "slow", "average", "fast" (predefined ranges).
        """
        await ctx.defer(ephemeral=False)
        drop_channels = self.bot.data.setdefault("drop_channels", {})
        display_name = get_puzzle_display_name(self.bot.data, puzzle) if puzzle != "all_puzzles" else "All Puzzles"

        final_mode = (mode or self.DEFAULT_DROP_MODE).lower()

        # Helper to parse value ranges
        if final_mode == "timer":
            # interpret value as minutes (or range of minutes)
            parsed = self._parse_range_value(value, as_minutes=True) if value is not None else None
        else:
            parsed = self._parse_range_value(value, as_minutes=False) if value is not None else None

        # --- Timer mode ---
        if final_mode == "timer":
            final_value = parsed if parsed is not None else (self.DEFAULT_DROP_TIMER_MINUTES * 60)
            drop_channels[str(channel.id)] = {
                "puzzle": puzzle,
                "mode": "timer",
                "value": final_value,
                "last_drop_time": datetime.now(timezone.utc).isoformat(),
            }
            summary = f"every {final_value // 60} minutes"

        # --- Messages mode ---
        elif final_mode == "messages":
            final_value = parsed if parsed is not None else self.DEFAULT_DROP_MESSAGE_COUNT
            drop_channels[str(channel.id)] = {
                "puzzle": puzzle,
                "mode": "messages",
                "value": final_value,
                "message_count": 0,
                "next_trigger": final_value
            }
            summary = f"every {final_value} messages"

        # --- Random / Frequency mode with predefined levels ---
        elif final_mode == "random":
            level = (frequency_level or "average").lower()
            if level not in FREQUENCY_LEVELS:
                level = "average"
            ranges = FREQUENCY_LEVELS[level]
            min_secs, max_secs = ranges["time"]
            min_msgs, max_msgs = ranges["messages"]
            # Keep existing behavior of using "all_puzzles" for random frequency mode.
            drop_channels[str(channel.id)] = {
                "puzzle": "all_puzzles",
                "mode": "random",
                "trigger": "frequency",
                "frequency_level": level,
                "last_drop_time": datetime.now(timezone.utc).isoformat(),
                "next_trigger_time": random.randint(min_secs, max_secs),
                "next_trigger_messages": random.randint(min_msgs, max_msgs),
                "message_count": 0,
            }
            summary = f"{level} random: every {min_secs//60}-{max_secs//60} minutes OR {min_msgs}-{max_msgs} messages"

        else:
            return await ctx.send("❌ Invalid mode. Use 'timer', 'messages', or 'random'.", ephemeral=True)

        save_data(self.bot.data)
        await ctx.send(
            f"✅ Drops for **{display_name}** configured in {channel.mention}.\n"
            f"Mode: `{final_mode}` | Trigger: `{summary}`.",
            ephemeral=False
        )
        await log(
            self.bot,
            f"🔧 Drop channel configured for **{display_name}** in `#{channel.name}` by `{ctx.author}`.")

    @commands.hybrid_command(name="removedropchannel", description="Remove a channel from automatic puzzle drops.")
    @is_admin()
    async def removedropchannel(self, ctx: commands.Context, channel: discord.TextChannel):
        drop_channels = self.bot.data.get("drop_channels", {})
        if str(channel.id) in drop_channels:
            drop_channels.pop(str(channel.id))
            save_data(self.bot.data)
            await ctx.send(f"❌ Drop channel removed: {channel.mention}", ephemeral=False)
            await log(
                self.bot,
                f"🔧 Drop channel removed for `#{channel.name}` by `{ctx.author}`."
            )
        else:
            await ctx.send(f"Channel {channel.mention} is not configured for drops.", ephemeral=True)

    @commands.hybrid_command(name="listdropchannels", description="List configured drop channels and settings.")
    @is_admin()
    async def listdropchannels(self, ctx: commands.Context):
        """Shows the current drop configuration for all channels."""
        drop_channels = self.bot.data.get("drop_channels", {})
        if not drop_channels:
            return await ctx.send("No drop channels are configured.", ephemeral=True)

        def fmt_seconds(s: Optional[int]) -> str:
            if s is None:
                return "—"
            s = int(s)
            if s < 60:
                return f"{s}s"
            m, sec = divmod(s, 60)
            if m < 60:
                return f"{m}m{sec}s" if sec else f"{m}m"
            h, m = divmod(m, 60)
            return f"{h}h{m}m" if m else f"{h}h"

        def time_until(next_ts_iso: Optional[str], offset_seconds: Optional[int]) -> str:
            if not next_ts_iso or offset_seconds is None:
                return "—"
            try:
                last = datetime.fromisoformat(next_ts_iso)
                target = last + timedelta(seconds=int(offset_seconds))
                now = datetime.now(timezone.utc)
                if target <= now:
                    return "due now"
                delta = target - now
                return fmt_seconds(int(delta.total_seconds()))
            except Exception:
                return "—"

        embed = discord.Embed(title="Configured Drop Channels", color=Colors.THEME_COLOR)
        now = datetime.now(timezone.utc)

        for ch_id_str, cfg in drop_channels.items():
            try:
                ch_id = int(ch_id_str)
            except Exception:
                continue
            # channel mention
            ch_mention = f"<#{ch_id}>"

            mode = cfg.get("mode", "timer")
            puzzle = cfg.get("puzzle", "—")
            puzzle_display = puzzle
            if puzzle and puzzle != "all_puzzles":
                try:
                    puzzle_key = resolve_puzzle_key(self.bot.data, puzzle)
                    if puzzle_key:
                        puzzle_display = get_puzzle_display_name(self.bot.data, puzzle_key)
                except Exception:
                    puzzle_display = puzzle
            elif puzzle == "all_puzzles":
                puzzle_display = "All Puzzles"

            # Build a human readable description per mode
            desc_lines = []
            desc_lines.append(f"Puzzle: `{puzzle}` — **{puzzle_display}**")
            desc_lines.append(f"Mode: `{mode}`")

            if mode == "timer":
                value = cfg.get("value")
                if value:
                    desc_lines.append(f"Timer value: {fmt_seconds(value)}")
                    last_ts = cfg.get("last_drop_time")
                    if last_ts:
                        time_left = time_until(last_ts, value)
                        desc_lines.append(f"Next in: {time_left}")
                else:
                    desc_lines.append("Timer value: default")
            elif mode == "messages":
                val = cfg.get("value", cfg.get("next_trigger", None))
                msg_count = cfg.get("message_count", 0)
                desc_lines.append(f"Trigger every {val} messages")
                desc_lines.append(f"Progress: {msg_count}/{val}")
            elif mode == "random" and cfg.get("trigger") == "frequency":
                level = cfg.get("frequency_level", "average")
                ranges = FREQUENCY_LEVELS.get(level, FREQUENCY_LEVELS["average"])
                min_secs, max_secs = ranges["time"]
                min_msgs, max_msgs = ranges["messages"]
                desc_lines.append(f"Frequency level: `{level}`")
                desc_lines.append(f"Level ranges: {min_msgs}-{max_msgs} messages or {min_secs//60}-{max_secs//60} minutes")
                next_time = cfg.get("next_trigger_time")
                next_msgs = cfg.get("next_trigger_messages")
                msg_count = cfg.get("message_count", 0)
                if next_time is not None:
                    desc_lines.append(f"Current next time: {fmt_seconds(next_time)} (in {time_until(cfg.get('last_drop_time'), next_time)})")
                if next_msgs is not None:
                    desc_lines.append(f"Current next messages: {next_msgs} (progress {msg_count}/{next_msgs})")
            else:
                desc_lines.append("Unknown or unsupported mode configuration.")

            embed.add_field(name=ch_mention, value="\n".join(desc_lines), inline=False)

        try:
            await ctx.send(embed=embed, ephemeral=True)
        except Exception:
            # fallback to plaintext if embed fails
            lines = []
            for field in embed.fields:
                lines.append(f"{field.name}:\n{field.value}\n")
            await ctx.send("Configured Drop Channels:\n\n" + "\n".join(lines), ephemeral=True)


# --- Cog entry point ---
async def setup(bot: commands.Bot):
    await bot.add_cog(PuzzleDropsCog(bot))