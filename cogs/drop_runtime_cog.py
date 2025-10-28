import asyncio
import inspect
import json
import logging
import os
import random
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional
import discord
from discord import app_commands
from discord.ui import View, Button
from discord.ext import commands
from cogs.constants import BASE_DIR, LOG_CHANNEL_ID
from cogs.db_utils import load_data, save_data, get_drop_channels, slugify_key, get_puzzle, resolve_puzzle_folder, get_channel_puzzle_slug, validate_puzzle_config  # adapt as needed
from cogs.drop_config import DropConfig
from ui.overlay import render_progress_image
logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.DEBUG,  # or INFO if you want less noise
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

logger.warning("🧪 [COG NAME] loaded")

logger = logging.getLogger("alice.drops")
logger.setLevel(logging.INFO)

os.makedirs("temp", exist_ok=True)
BASE_DIR = Path(os.getcwd())
_SAVE_LOCK = threading.Lock()
LOG_CHANNEL_ID: Optional[int] = None  # set if you want a dedicated log channel id

def safe_append_piece(data: dict, uid: str, puzzle_slug: str, piece_idx: str) -> bool:
    """
    Safely appends a puzzle piece to a user's collection.
    Returns True if the piece was newly added, False if it was already owned.
    """
    user_pieces = data.setdefault("user_pieces", {})
    puzzle_map = user_pieces.setdefault(uid, {})
    piece_list = puzzle_map.setdefault(puzzle_slug, [])

    if piece_idx in piece_list:
        return False

    piece_list.append(piece_idx)
    return True

def safe_claim_summary(puzzle_slug: str, piece_idx: str, claimants: list[discord.User]) -> tuple[str, discord.Embed]:
    """
    Builds a summary message and embed for a completed drop.
    Returns (text_message, embed_object)
    """
    mentions = ", ".join(u.mention for u in claimants)
    summary_text = f"🧩 Piece `{piece_idx}` from **{puzzle_slug}** claimed by: {mentions}"

    embed = discord.Embed(
        title="🧩 Drop Claimed",
        description=f"**Puzzle:** {puzzle_slug}\n**Piece:** {piece_idx}",
        color=discord.Color.green(),
        timestamp=datetime.utcnow()
    )
    embed.add_field(name="Claimants", value="\n".join(u.mention for u in claimants), inline=False)
    embed.set_footer(text="Drop completed")

    return summary_text, embed

def save_collected(bot: commands.Bot):
    try:
        with _SAVE_LOCK:
            save_data(bot.collected)
    except Exception:
        logger.exception("Failed to save collected data")

class DropView(discord.ui.View):
    def __init__(self, cog, puzzle: str, piece_idx: int, limit: int, log_channel=None):
        super().__init__(timeout=30)
        self.cog = cog
        self.puzzle = puzzle
        self.piece_idx = piece_idx
        self.limit = limit
        self.claimants = []
        self.completed = False
        self._completion_lock = asyncio.Lock()
        self.log_channel = log_channel
        self.message: Optional[discord.Message] = None  # set later

    async def on_timeout(self):
        self.clear_items()
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                logger.exception("Failed to edit message on timeout")

    @discord.ui.button(label="Collect Piece 🧩", style=discord.ButtonStyle.green)
    async def collect(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        uid = str(user.id)
        logger.debug("Collect clicked by %s on puzzle=%s piece=%s (limit=%s)", uid, self.puzzle, self.piece_idx,
                     self.limit)

        data = getattr(self.cog.bot, "collected", {})
        data.setdefault("user_pieces", {})
        data["user_pieces"].setdefault(uid, {})
        added = safe_append_piece(data, uid, self.puzzle, str(self.piece_idx))
        if not added:
            await interaction.response.send_message("❌ You already have this piece!", ephemeral=True)
            return

        try:
            with _SAVE_LOCK:
                save_data(self.cog.bot.collected)
        except Exception:
            logger.exception("Failed to save collected data")

        try:
            puzzles_map = getattr(self.cog.bot, "data", {}) or {}
            puzzle_cfg = puzzles_map.get(self.puzzle, {}) or {}
            display_name = puzzle_cfg.get("display_name", self.puzzle)

            puzzle_folder = resolve_puzzle_folder(self.puzzle, display_name)
            rows = int(puzzle_cfg.get("rows") or puzzle_cfg.get("r") or 4)
            cols = int(puzzle_cfg.get("cols") or puzzle_cfg.get("c") or 4)

            owned = list(self.cog.bot.collected.get("user_pieces", {}).get(uid, {}).get(self.puzzle, []))
            owned = [str(x) for x in owned]
            piece_map = self.cog.bot.data.get("pieces", {}).get(self.puzzle, {})

            preview_path = os.path.join("temp", f"{self.puzzle}_{uid}_progress.png")
            os.makedirs("temp", exist_ok=True)

            render_progress_image(
                puzzle_folder=puzzle_folder,
                collected_piece_ids=owned,
                rows=rows,
                cols=cols,
                puzzle_config=puzzle_cfg,
                output_path=preview_path,
                piece_map=piece_map,
            )
            logger.info("🖼️ Preview updated for %s/%s → %s", self.puzzle, uid, preview_path)

        except Exception:
            logger.exception("Pre-warm render failed for %s/%s", self.puzzle, uid)
            await interaction.response.send_message(
                "⚠️ Failed to render preview, but your piece was collected.",
                ephemeral=True
            )
            return

        self.claimants.append(user)
        self.clear_items()
        await interaction.response.edit_message(
            content=f"✅ You collected piece `{self.piece_idx}` for **{self.puzzle}**!",
            view=self
        )

        if len(self.claimants) >= self.limit:
            async with self._completion_lock:
                if not self.completed:
                    self.completed = True
                    await self.complete(interaction)

            embed = discord.Embed(
                title="🧩 Drop Claimed",
                description=f"**Puzzle:** {self.puzzle}\n**Piece:** {self.piece_idx}",
                color=discord.Color.green(),
                timestamp=datetime.utcnow()
            )
            embed.add_field(name="Claimants", value="\n".join(u.mention for u in self.claimants), inline=False)
            embed.set_footer(text="Drop completed")

            if self.log_channel:
                try:
                    await self.log_channel.send(embed=embed)
                except Exception:
                    logger.exception("Failed to send drop log")

            try:
                self.cog.recent_claims.append({
                    "timestamp": datetime.utcnow(),
                    "puzzle": self.puzzle,
                    "piece": self.piece_idx,
                    "claimants": [c for c in self.claimants]
                })
                self.cog.recent_claims = self.cog.recent_claims[-25:]
            except Exception:
                logger.exception("Failed to append recent_claims")


class DropRuntime(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._scheduler_task: Optional[asyncio.Task] = None
        self.recent_claims = []  # shared recent claims, updated by DropView

    @commands.Cog.listener()
    async def on_ready(self):
        if self._scheduler_task is None or self._scheduler_task.done():
            self._scheduler_task = asyncio.create_task(self._drop_scheduler())
            logger.info("Requested start of drop scheduler task")

    async def cog_unload(self):
        if self._scheduler_task:
            self._scheduler_task.cancel()
            try:
                await self._scheduler_task
            except asyncio.CancelledError:
                pass
            logger.info("Drop scheduler task cancelled on cog unload")

    async def puzzle_autocomplete(self, interaction: discord.Interaction, current: str):
        puzzles = getattr(interaction.client, "data", {}).get("puzzles", {})
        choices = []
        for slug, meta in puzzles.items():
            display = (meta or {}).get("display_name") or slug.replace("_", " ").title()
            if current.lower() in slug.lower() or current.lower() in display.lower():
                choices.append(app_commands.Choice(name=display, value=slug))
        return choices[:25]

    async def piece_autocomplete(self, interaction: discord.Interaction, current: str):
        requested = None
        try:
            requested = getattr(interaction.namespace, "puzzle", None)
        except Exception:
            requested = None

        puzzles = getattr(interaction.client, "data", {}).get("puzzles", {}) or {}
        puzzle_key = None
        if requested:
            puzzle_key = requested
            if requested not in puzzles:
                for k, m in puzzles.items():
                    if requested.lower() in (m.get("display_name", "") or "").lower():
                        puzzle_key = k
                        break

        if not puzzle_key:
            return []

        pieces_map = getattr(interaction.client, "data", {}).get("pieces", {}).get(puzzle_key, {}) or {}
        choices = []
        for idx, fname in pieces_map.items():
            label = f"Piece {idx}"
            if current.lower() in label.lower() or current.lower() in str(idx):
                choices.append(app_commands.Choice(name=label, value=str(idx)))
        return choices[:25]

    async def _spawn_drop_for_channel(self, channel: discord.TextChannel, cfg: dict):
        try:
            if not cfg:
                logger.warning("No drop config found for channel %s — using default fallback", channel.id)
                cfg = {
                    "mode": "timer",
                    "value": random.randint(180, 600),
                    "puzzle": "All Puzzles"
                }

            config = DropConfig(self.bot, channel.id, cfg)
            if not config.slug:
                logger.warning("Drop config missing puzzle key for channel %s", channel.id)
                return False

            meta = config.meta
            display = config.display
            if meta is None:
                logger.warning("Puzzle slug not found in puzzles data: %s", config.slug)
                return False

            display = (meta or {}).get("display_name", config.slug.replace("_", " ").title())

            pieces_map = getattr(self.bot, "data", {}).get("pieces", {}).get(config.slug, {}) or {}
            if not pieces_map:
                logger.warning("No pieces configured for puzzle %s", config.slug)
                return False

            piece_idx = random.choice(list(pieces_map.keys()))
            piece_file = pieces_map.get(str(piece_idx))

            embed = discord.Embed(
                title=f"🧩 {display}",
                description=f"Collect a piece of **{display}**",
                color=discord.Color.purple()
            )

            file = None
            if piece_file:
                piece_path = Path("puzzles/alice_test/alice_test_base.png")
                clean_name = "alice_test_base.png"
                file = discord.File(str(piece_path), filename=clean_name)

                if not piece_path.is_absolute():
                    piece_path = BASE_DIR.joinpath(piece_file)
                if piece_path.exists():

                    try:
                        clean_name = f"{config.slug}_{piece_idx}.png"
                        logger.debug("Resolved piece path: %s", piece_path)
                        logger.debug("Using attachment filename: %s", clean_name)
                        logger.warning("🧪 drop_runtime_cog: attaching image from %s", piece_path)
                        file = discord.File(str(piece_path), filename=clean_name)
                        embed.set_image(url=f"attachment://{clean_name}")
                    except Exception:
                        logger.exception("Failed to attach piece image %s", piece_path)

            raw_range = cfg.get("claims_range") or [1, 3]
            try:
                low, high = int(raw_range[0]), int(raw_range[1])
                if low > high:
                    low, high = high, low
            except Exception:
                low, high = 1, 3
            limit = random.randint(low, high)

            view = DropView(
                puzzle_slug=config.slug,
                piece_idx=str(piece_idx),
                limit=limit,
                log_channel=self.bot.get_channel(LOG_CHANNEL_ID) if LOG_CHANNEL_ID else None,
                cog=self
            )

            if file:
                await channel.send(embed=embed, file=file, view=view)
            else:
                await channel.send(embed=embed, view=view)
            logger.info("Spawned drop for '%s' in channel %s", config.slug, channel.id)
            return True

        except discord.Forbidden:
            logger.warning("Missing send permission in channel %s", getattr(channel, "id", "unknown"))
            return False
        except Exception:
            logger.exception("Unexpected error when spawning drop for channel %s", getattr(channel, "id", "unknown"))
            return False

    async def _drop_scheduler(self):
        await self.bot.wait_until_ready()
        logger.info("Drop scheduler started")
        while not self.bot.is_closed():
            try:
                drop_channels = getattr(self.bot, "data", {}).get("drop_channels", {}) or {}
                if not drop_channels:
                    logger.debug("No drop channels configured; sleeping")
                    await asyncio.sleep(30)
                    continue

                for ch_str, cfg in list(drop_channels.items()):
                    try:
                        channel = self.bot.get_channel(int(ch_str))
                        if channel is None:
                            logger.warning("Configured channel not found: %s", ch_str)
                            continue

                        config = DropConfig(self.bot, int(ch_str), cfg)
                        if not config.slug:
                            continue
                        if not config.meta:
                            logger.warning("Configured puzzle slug not found for channel %s: %s", ch_str, config.slug)
                            continue

                        value = int(cfg.get("value", 1) or 1)
                        chance = min(100, max(1, value))
                        roll = random.randint(1, 100)
                        if config.roll_trigger():
                            await self._spawn_drop_for_channel(channel, config.raw)
                        else:
                            logger.debug("Chance miss for %s in %s (%s <= %s?)", config.slug, ch_str, roll, chance)

                    except Exception:
                        logger.exception("Error handling drop channel %s", ch_str)

                await asyncio.sleep(30)

            except Exception:
                logger.exception("Outer drop scheduler loop error")
                await asyncio.sleep(10)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        if message.content.startswith(self.bot.command_prefix) or message.content.startswith("/"):
            return

        ch_id = str(message.channel.id)
        cfg = self.bot.data.get("drop_channels", {}).get(ch_id)
        if not cfg or cfg.get("mode") != "messages":
            return

        config = DropConfig(self.bot, message.channel.id, cfg)
        config.increment_message()

        if config.message_count >= config.next_trigger:
            success = await self._spawn_drop_for_channel(message.channel, config.raw)
            if success:
                config.reset_trigger()

                logger.debug("🎲 Next drop in %s messages (trigger at %s)", config.next_trigger - config.message_count,
                             config.next_trigger)
                logger.debug("🔍 Drop config for channel %s: %s", ch_id, config.raw)

    @commands.command(name="validate_config")
    @commands.is_owner()
    async def validate_config(self, ctx):
        validate_puzzle_config(self.bot.data)
        await ctx.send("✅ Puzzle config validated. Check logs for warnings.")

    @commands.command(name="debug_spawn")
    @commands.is_owner()
    async def debug_spawn(self, ctx: commands.Context, channel: discord.TextChannel = None):
        drop_channels = getattr(self.bot, "data", {}).get("drop_channels", {}) or {}
        if not drop_channels:
            await ctx.send("No drop channels configured.")
            return

        if channel:
            key = str(channel.id)
            if key not in drop_channels:
                await ctx.send("That channel is not configured as a drop channel.")
                return
            cfg = drop_channels[key]
            channel_obj = channel
        else:
            key, cfg = next(iter(drop_channels.items()))
            channel_obj = self.bot.get_channel(int(key))

        if channel_obj is None:
            await ctx.send("Configured channel not found.")
            return

        ok = await self._spawn_drop_for_channel(channel_obj, cfg)
        await ctx.send("Spawn attempted." if ok else "Spawn failed; check logs.")


async def setup(bot: commands.Bot):
    await bot.add_cog(DropRuntime(bot))

