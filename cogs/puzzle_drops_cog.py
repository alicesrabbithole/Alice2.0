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
    get_puzzle_display_name
)
from utils.log_utils import log
from ui.views import DropView
from utils.checks import is_admin
from utils.theme import Colors

logger = logging.getLogger(__name__)

FREQUENCY_COMBINED_RANGES = {
    "high": {
        "time": (2 * 60, 7 * 60),        # seconds
        "messages": (7, 15),
    },
    "medium": {
        "time": (10 * 60, 15 * 60),      # seconds
        "messages": (30, 50),
    },
    "low": {
        "time": (16 * 60, 30 * 60),
        "messages": (60, 90),
    },
}

def get_embed_color(bot, puzzle_key) -> int:
    meta = bot.data.get("puzzles", {}).get(puzzle_key, {})
    color_str = meta.get("color")
    if color_str:
        try:
            color_str = str(color_str).lower().replace("#", "")
            if color_str.startswith("0x"):
                color_str = color_str[2:]
            return int(color_str, 16)
        except Exception:
            pass
    return Colors.THEME_COLOR  # Fallback

def get_ping_role_id(bot, puzzle_key) -> Optional[int]:
    meta = bot.data.get("puzzles", {}).get(puzzle_key, {})
    role_meta = meta.get("ping_role_id")
    if role_meta:
        try:
            return int(role_meta)
        except Exception:
            pass
    return getattr(config, "PUZZLE_PING_ROLE_ID", None)

def get_reward_role_id(bot, puzzle_key) -> Optional[int]:
    meta = bot.data.get("puzzles", {}).get(puzzle_key, {})
    role_meta = meta.get("reward_role_id")
    if role_meta:
        try:
            return int(role_meta)
        except Exception:
            pass
    return getattr(config, "PUZZLE_REWARD_ROLE_ID", None)

class PuzzleDropsCog(commands.Cog, name="Puzzle Drops"):
    """Manages the automatic and manual dropping of puzzle pieces, pings and rewards."""

    DEFAULT_DROP_MODE = "timer"
    DEFAULT_DROP_TIMER_MINUTES = 15
    DEFAULT_DROP_MESSAGE_COUNT = 40

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.drop_scheduler.start()

    def cog_unload(self):
        self.drop_scheduler.cancel()

    async def puzzle_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> List[app_commands.Choice[str]]:
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
            logger.error(f"FATAL: Unhandled exception in puzzle_autocomplete: {e}")
            return []
        return choices[:25]

    async def _spawn_drop(
        self, channel: discord.TextChannel, puzzle_key: str, forced_piece: Optional[str] = None
    ):
        meta = self.bot.data.get("puzzles", {}).get(puzzle_key, {})
        display_name = meta.get("display_name", puzzle_key.replace("_", " ").title())
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

        emoji = config.CUSTOM_EMOJI_STRING or config.DEFAULT_EMOJI

        embed_color = get_embed_color(self.bot, puzzle_key)
        embed = (
            discord.Embed(
                title=f"{emoji} A Wild Puzzle Piece Appears!",
                description=f"A piece of the **{display_name}** puzzle has dropped!\nClick the button to collect it.",
                color=embed_color,
            ).set_image(url="attachment://puzzle_piece.png")
        )

        raw_cfg = self.bot.data.get("drop_channels", {}).get(str(channel.id), {})
        claims_range = raw_cfg.get("claims_range", [1, 3])
        claim_limit = random.randint(claims_range[0], claims_range[1])
        view = DropView(self.bot, puzzle_key, display_name, piece_id, claim_limit)

        ping_role_id = get_ping_role_id(self.bot, puzzle_key)
        ping_content = f"<@&{ping_role_id}>" if ping_role_id else None

        try:
            message = await channel.send(
                content=ping_content,
                embed=embed,
                file=file,
                view=view
            )
            view.message = message
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.exception(f"Failed to send drop in #{channel.name}: {e}")

    async def award_completion_role(self, user: discord.Member, puzzle_key: str):
        role_id = get_reward_role_id(self.bot, puzzle_key)
        if not role_id:
            logger.info(f"No reward role configured for puzzle {puzzle_key}")
            return
        role = user.guild.get_role(role_id)
        if not role:
            logger.warning(f"Reward role {role_id} not found in guild {user.guild.name}")
            return
        try:
            await user.add_roles(role, reason="Completed the puzzle")
            logger.info(f"Role {role.name} awarded to {user} for completing {puzzle_key}")
        except discord.Forbidden:
            logger.error(f"Failed to award role {role.name} to {user}: missing permissions")
        except Exception as e:
            logger.error(f"Error awarding role: {e}")

    @tasks.loop(seconds=30)
    async def drop_scheduler(self):
        await self.bot.wait_until_ready()
        now = datetime.now(timezone.utc)
        drop_channels = self.bot.data.get("drop_channels", {})
        data_changed = False

        for ch_id_str, raw_cfg in drop_channels.items():
            if raw_cfg.get("mode") == "random" and raw_cfg.get("trigger") == "frequency":
                channel = self.bot.get_channel(int(ch_id_str))
                freq = raw_cfg.get("frequency_mode", "medium")
                ranges = FREQUENCY_COMBINED_RANGES.get(freq, FREQUENCY_COMBINED_RANGES["medium"])
                min_secs, max_secs = ranges["time"]
                min_msgs, max_msgs = ranges["messages"]

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
                if last_drop_str:
                    last_drop_time = datetime.fromisoformat(last_drop_str)
                    time_ready = now >= last_drop_time + timedelta(seconds=raw_cfg["next_trigger_time"])
                else:
                    raw_cfg["last_drop_time"] = now.isoformat()
                    data_changed = True
                    continue

                # If time triggers first and messages not hit yet
                if time_ready and raw_cfg["message_count"] < raw_cfg["next_trigger_messages"]:
                    all_puzzles = list(self.bot.data.get("puzzles", {}).keys())
                    puzzle_key = random.choice(all_puzzles) if all_puzzles else None
                    if puzzle_key:
                        await self._spawn_drop(channel, puzzle_key)
                        raw_cfg["last_drop_time"] = now.isoformat()
                        raw_cfg["next_trigger_time"] = random.randint(min_secs, max_secs)
                        raw_cfg["next_trigger_messages"] = random.randint(min_msgs, max_msgs)
                        raw_cfg["message_count"] = 0
                        data_changed = True
                    continue
        if data_changed:
            save_data(self.bot.data)

    # -------- Listener: checks message interval --------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        raw_cfg = self.bot.data.get("drop_channels", {}).get(str(message.channel.id))
        if not raw_cfg:
            return

        if raw_cfg.get("mode") == "random" and raw_cfg.get("trigger") == "frequency":
            freq = raw_cfg.get("frequency_mode", "medium")
            ranges = FREQUENCY_COMBINED_RANGES.get(freq, FREQUENCY_COMBINED_RANGES["medium"])
            min_secs, max_secs = ranges["time"]
            min_msgs, max_msgs = ranges["messages"]

            raw_cfg["message_count"] = raw_cfg.get("message_count", 0) + 1
            next_msgs = raw_cfg.get("next_trigger_messages", random.randint(min_msgs, max_msgs))

            now = datetime.now(timezone.utc)
            last_drop_str = raw_cfg.get("last_drop_time")
            if last_drop_str:
                last_drop_time = datetime.fromisoformat(last_drop_str)
                time_ready = now >= last_drop_time + timedelta(
                    seconds=raw_cfg.get("next_trigger_time", random.randint(min_secs, max_secs)))
            else:
                raw_cfg["last_drop_time"] = now.isoformat()
                return

            # If messages triggers first and time threshold not hit yet
            if raw_cfg["message_count"] >= next_msgs and not time_ready:
                all_puzzles = list(self.bot.data.get("puzzles", {}).keys())
                puzzle_key = random.choice(all_puzzles) if all_puzzles else None
                if puzzle_key:
                    await self._spawn_drop(message.channel, puzzle_key)
                    raw_cfg["last_drop_time"] = datetime.now(timezone.utc).isoformat()
                    raw_cfg["next_trigger_time"] = random.randint(min_secs, max_secs)
                    raw_cfg["next_trigger_messages"] = random.randint(min_msgs, max_msgs)
                    raw_cfg["message_count"] = 0
                    save_data(self.bot.data)
            return

    @commands.hybrid_command(name="spawndrop", description="Manually spawn a puzzle drop.")
    @app_commands.autocomplete(puzzle=puzzle_autocomplete)
    @is_admin()
    async def spawndrop(
        self, ctx: commands.Context, puzzle: str, channel: Optional[discord.TextChannel] = None
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

        await self._spawn_drop(target_channel, puzzle_key)
        display_name = get_puzzle_display_name(self.bot.data, puzzle_key)
        await ctx.send(f"✅ Drop for **{display_name}** spawned in {target_channel.mention}.", ephemeral=True)

    @commands.hybrid_command(name="setdropchannel", description="Configure a channel for automatic puzzle drops.")
    @app_commands.autocomplete(puzzle=puzzle_autocomplete)
    @is_admin()
    async def setdropchannel(
            self,
            ctx: commands.Context,
            channel: discord.TextChannel,
            puzzle: str,
            mode: Optional[str] = None,
            value: Optional[int] = None,
            trigger: Optional[str] = None,  # New: "timer", "messages", "frequency"
            frequency_mode: Optional[str] = None,  # New: "low", "medium", "high"
    ):
        await ctx.defer(ephemeral=False)
        if puzzle != "all_puzzles":
            puzzle_key = resolve_puzzle_key(self.bot.data, puzzle)
            if not puzzle_key:
                return await ctx.send(f"❌ Puzzle not found: `{puzzle}`", ephemeral=True)

        final_mode = mode or self.DEFAULT_DROP_MODE
        trigger_type = trigger or ("frequency" if final_mode in FREQUENCY_COMBINED_RANGES else final_mode)
        freq_mode = frequency_mode or (final_mode if final_mode in FREQUENCY_COMBINED_RANGES else None)

        drop_channels = self.bot.data.setdefault("drop_channels", {})

        if trigger_type == "messages":
            # message drops (fixed)
            msg_trigger = value if value is not None else self.DEFAULT_DROP_MESSAGE_COUNT
            drop_channels[str(channel.id)] = {
                "puzzle": puzzle,
                "mode": "messages",
                "trigger": "messages",
                "value": msg_trigger,
                "last_drop_time": datetime.now(timezone.utc).isoformat(),
                "message_count": 0,
                "next_trigger_messages": msg_trigger,
            }
            summary = f"{msg_trigger} messages"
        elif trigger_type == "timer":
            # timer drops (fixed)
            mins_trigger = value if value is not None else self.DEFAULT_DROP_TIMER_MINUTES
            drop_channels[str(channel.id)] = {
                "puzzle": puzzle,
                "mode": "timer",
                "trigger": "timer",
                "value": mins_trigger * 60,
                "last_drop_time": datetime.now(timezone.utc).isoformat(),
                "next_trigger_time": mins_trigger * 60,
            }
            summary = f"{mins_trigger} minutes"
        elif trigger_type == "frequency" and freq_mode in FREQUENCY_COMBINED_RANGES:
            # frequency drops (random time OR messages)
            time_range = FREQUENCY_COMBINED_RANGES[freq_mode]["time"]
            msg_range = FREQUENCY_COMBINED_RANGES[freq_mode]["messages"]
            next_time = random.randint(*time_range)
            next_msgs = random.randint(*msg_range)
            drop_channels[str(channel.id)] = {
                "puzzle": "all_puzzles",
                "mode": "random",
                "trigger": "frequency",
                "frequency_mode": freq_mode,
                "last_drop_time": datetime.now(timezone.utc).isoformat(),
                "next_trigger_time": next_time,
                "next_trigger_messages": next_msgs,
                "message_count": 0,
            }
            summary = f"random every {time_range[0] // 60}-{time_range[1] // 60} minutes or {msg_range[0]}-{msg_range[1]} messages"
        else:
            return await ctx.send(
                "❌ Invalid mode. Use `timer`, `messages`, or a frequency preset (`low`, `medium`, `high`).",
                ephemeral=True)

        display_name = get_puzzle_display_name(self.bot.data, puzzle) if puzzle != "all_puzzles" else "All Puzzles"
        await ctx.send(
            f"✅ Drops for **{display_name}** are now configured in {channel.mention}.\n"
            f"Mode: `{trigger_type}` | Trigger: `{summary}`."
        )
        await log(
            self.bot,
            f"🔧 Drop channel configured for **{display_name}** in `#{channel.name}` by `{ctx.author}`."
        )

    @commands.hybrid_command(
        name="removedropchannel",
        description="Remove a channel from automatic puzzle drops."
    )
    @is_admin()
    async def removedropchannel(
            self,
            ctx: commands.Context,
            channel: discord.TextChannel
    ):
        drop_channels = self.bot.data.get("drop_channels", {})
        if str(channel.id) not in drop_channels:
            return await ctx.send(
                f"❌ {channel.mention} is not configured as a drop channel.",
                ephemeral=True
            )
        del drop_channels[str(channel.id)]
        save_data(self.bot.data)
        await ctx.send(
            f"✅ Drops have been disabled for {channel.mention}.",
            ephemeral=False
        )
        await log(
            self.bot,
            f"🗑️ Drop channel removed: `#{channel.name}` by `{ctx.author}`."
        )

    # -- Example call on puzzle completion. Place wherever appropriate in your puzzle completion logic --
    async def handle_puzzle_completion(self, user: discord.Member, puzzle_key: str):
        # Existing puzzle completion code here...
        await self.award_completion_role(user, puzzle_key)
        # (any other completion actions...)

# --- Cog entry point ---
async def setup(bot: commands.Bot):
    await bot.add_cog(PuzzleDropsCog(bot))