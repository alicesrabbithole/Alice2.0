import discord
from discord.ext import commands, tasks
from discord import app_commands, Interaction
import random, io, logging
from typing import Optional
from datetime import datetime, timezone, timedelta
from PIL import Image

from .utils.db_utils import save_data, resolve_puzzle_key, get_puzzle_display_name, is_staff
from .utils.log_utils import log
from .ui.views import DropView, CUSTOM_EMOJI_STRING, DEFAULT_EMOJI

logger = logging.getLogger(__name__)

DEFAULT_TIMER_RANGE = (30, 60)
DEFAULT_MESSAGE_RANGE = (100, 200)


async def puzzle_autocomplete(interaction: Interaction, current: str) -> list[app_commands.Choice[str]]:
    puzzles = getattr(interaction.client, "data", {}).get("puzzles", {}) or {}
    choices = [app_commands.Choice(name="All Puzzles (Random)", value="All Puzzles")] if "all puzzles".startswith(
        current.lower()) else []
    for slug, _ in puzzles.items():
        display_name = get_puzzle_display_name({}, slug)
        if current.lower() in slug.lower() or current.lower() in display_name.lower():
            choices.append(app_commands.Choice(name=display_name, value=slug))
    return choices[:25]


class PuzzleDropsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.drop_scheduler.start()

    def cog_unload(self):
        self.drop_scheduler.cancel()

    async def _spawn_drop(self, channel: discord.TextChannel, puzzle_key: str, forced_piece: Optional[str] = None):
        display_name = get_puzzle_display_name(self.bot.data, puzzle_key)
        pieces_map = self.bot.data.get("pieces", {}).get(puzzle_key, {})
        if not pieces_map: return
        piece_id = forced_piece or random.choice(list(pieces_map.keys()))
        piece_path = pieces_map.get(piece_id)
        if not piece_path: return
        try:
            with Image.open(piece_path) as img:
                img.thumbnail((128, 128));
                buffer = io.BytesIO();
                img.save(buffer, "PNG");
                buffer.seek(0)
                file = discord.File(buffer, filename="puzzle_piece.png")
        except Exception:
            file = discord.File(piece_path, filename="puzzle_piece.png")
        emoji = CUSTOM_EMOJI_STRING or DEFAULT_EMOJI
        embed = discord.Embed(title=f"{emoji} A Wild Puzzle Piece Appears!",
                              description=f"A piece of the **{display_name}** puzzle has dropped! Click the button to collect it.",
                              color=discord.Color.purple()).set_image(url="attachment://puzzle_piece.png")
        raw_cfg = self.bot.data.get("drop_channels", {}).get(str(channel.id), {})
        claims_range = raw_cfg.get("claims_range", [1, 3])
        claim_limit = random.randint(claims_range[0], claims_range[1])
        view = DropView(self.bot, puzzle_key, display_name, piece_id, claim_limit)
        try:
            message = await channel.send(embed=embed, file=file, view=view)
            view.message = message
        except Exception as e:
            logger.exception(f"Failed to send drop in #{channel.name}: {e}")

    @tasks.loop(seconds=30)
    async def drop_scheduler(self):
        await self.bot.wait_until_ready()
        now = datetime.now(timezone.utc)
        for ch_id_str, raw_cfg in self.bot.data.get("drop_channels", {}).items():
            if raw_cfg.get("mode") != "timer": continue
            last_drop_str = raw_cfg.get("last_drop_time")
            if not last_drop_str:
                raw_cfg["last_drop_time"] = now.isoformat();
                save_data(self.bot.data);
                continue
            last_drop_time = datetime.fromisoformat(last_drop_str)
            seconds_to_wait = raw_cfg.get("value", 3600)
            if now >= last_drop_time + timedelta(seconds=seconds_to_wait):
                channel = self.bot.get_channel(int(ch_id_str))
                if not channel: continue
                puzzle_slug = raw_cfg.get("puzzle")
                if not puzzle_slug: continue
                if puzzle_slug.lower() == "all puzzles":
                    all_puzzles = list(self.bot.data.get("puzzles", {}).keys())
                    puzzle_key = random.choice(all_puzzles) if all_puzzles else None
                else:
                    puzzle_key = resolve_puzzle_key(self.bot.data, puzzle_slug)
                if puzzle_key:
                    await self._spawn_drop(channel, puzzle_key)
                    raw_cfg["last_drop_time"] = now.isoformat()
                    save_data(self.bot.data)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Ignore bots and DMs
        if message.author.bot or not message.guild:
            return

        # Handle message-based drops
        raw_cfg = self.bot.data.get("drop_channels", {}).get(str(message.channel.id))
        if raw_cfg and raw_cfg.get("mode") == "messages":
            raw_cfg["message_count"] = raw_cfg.get("message_count", 0) + 1
            if raw_cfg["message_count"] >= raw_cfg.get("next_trigger", raw_cfg.get("value")):
                puzzle_key = resolve_puzzle_key(self.bot.data, raw_cfg.get("puzzle"))
                if puzzle_key:
                    await self._spawn_drop(message.channel, puzzle_key)
                    raw_cfg["next_trigger"] = raw_cfg["message_count"] + raw_cfg.get("value")
            save_data(self.bot.data)

        # --- THIS IS THE FIX ---
        # After handling our custom logic, we must process the message for any commands.
        await self.bot.process_commands(message)

    @commands.hybrid_command(name="spawndrop", description="[Staff] Manually spawn a puzzle drop.")
    @app_commands.autocomplete(puzzle=puzzle_autocomplete)
    @commands.check(lambda ctx: is_staff(ctx.bot.data, ctx.author))
    async def spawndrop(self, ctx: commands.Context, puzzle: str, channel: Optional[discord.TextChannel] = None):
        await ctx.defer(ephemeral=True)
        target_channel = channel or ctx.channel
        puzzle_key = resolve_puzzle_key(self.bot.data, puzzle)
        if not puzzle_key: await ctx.send(f"❌ Puzzle not found: `{puzzle}`", ephemeral=True); return
        await self._spawn_drop(target_channel, puzzle_key)
        display_name = get_puzzle_display_name(self.bot.data, puzzle_key)
        await ctx.send(f"✅ Drop for **{display_name}** has been spawned in {target_channel.mention}.", ephemeral=True)

    @commands.hybrid_command(name="setdropchannel",
                             description="[Admin] Configure a channel for automatic puzzle drops.")
    @app_commands.autocomplete(puzzle=puzzle_autocomplete)
    @commands.has_permissions(administrator=True)
    async def setdropchannel(self, ctx: commands.Context, channel: discord.TextChannel, puzzle: str,
                             mode: Optional[str] = None, value: Optional[int] = None):
        await ctx.defer(ephemeral=True)
        final_mode = (mode or "timer").lower()
        if final_mode not in ["timer", "messages"]: await ctx.send(
            "❌ Invalid mode. Please choose 'timer' or 'messages'.", ephemeral=True); return
        puzzle_key = resolve_puzzle_key(self.bot.data, puzzle)
        if not puzzle_key and puzzle.lower() != "all puzzles": await ctx.send(f"❌ Puzzle not found: `{puzzle}`",
                                                                              ephemeral=True); return
        if value is None: value = random.randint(*DEFAULT_TIMER_RANGE) if final_mode == "timer" else random.randint(
            *DEFAULT_MESSAGE_RANGE)
        final_value = value * 60 if final_mode == "timer" else value
        drop_channels = self.bot.data.setdefault("drop_channels", {})
        drop_channels[str(channel.id)] = {"puzzle": puzzle, "mode": final_mode, "value": final_value,
                                          "last_drop_time": datetime.now(timezone.utc).isoformat()}
        save_data(self.bot.data)
        display_name = get_puzzle_display_name(self.bot.data, puzzle_key) if puzzle_key else "All Puzzles"
        await ctx.send(f"✅ Drops for **{display_name}** are now configured in {channel.mention}.", ephemeral=True)
        await log(self.bot, f"🔧 Drop channel configured for **{display_name}** in `#{channel.name}` by `{ctx.author}`.")

    # ... (rest of the commands are unchanged)


async def setup(bot: commands.Bot):
    await bot.add_cog(PuzzleDropsCog(bot))