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
from cogs.preview_cache import preview_cache_path, invalidate_user_puzzle_cache, render_progress_image
import discord
from discord import app_commands
from discord.ui import View, Button
from discord.ext import commands
from cogs.constants import BASE_DIR, LOG_CHANNEL_ID

from cogs.db_utils import save_data, get_drop_channels, slugify_key, get_puzzle  # adapt as needed
from cogs.preview_cache import preview_cache_path, invalidate_user_puzzle_cache, render_progress_image  # optional

logging.basicConfig(
    level=logging.DEBUG,  # or INFO if you want less noise
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

logger = logging.getLogger("alice.drops")
logger.setLevel(logging.INFO)

BASE_DIR = Path(os.getcwd())
_SAVE_LOCK = threading.Lock()
LOG_CHANNEL_ID: Optional[int] = None  # set if you want a dedicated log channel id


class DropView(View):
    def __init__(self, puzzle: str, piece_idx: str, limit: int, log_channel: Optional[discord.TextChannel], cog):
        super().__init__(timeout=None)
        self.puzzle = puzzle
        self.piece_idx = piece_idx
        self.limit = limit
        self.claimants: list[discord.Member] = []
        self.log_channel = log_channel
        self.cog = cog

    @discord.ui.button(label="Collect Piece 🧩", style=discord.ButtonStyle.green)
    async def collect(self, interaction: discord.Interaction, button: Button):
        user = interaction.user
        uid = str(user.id)
        logger.debug("Collect clicked by %s on puzzle=%s piece=%s (limit=%s)", uid, self.puzzle, self.piece_idx, self.limit)

        data = getattr(self.cog.bot, "collected", {})
        data.setdefault("user_pieces", {})
        data["user_pieces"].setdefault(uid, {})
        data["user_pieces"][uid].setdefault(self.puzzle, [])

        if self.piece_idx in data["user_pieces"][uid][self.puzzle]:
            await interaction.response.send_message("❌ You already have this piece!", ephemeral=True)
            return

        data["user_pieces"][uid][self.puzzle].append(self.piece_idx)
        try:
            with _SAVE_LOCK:
                save_data(getattr(self.cog.bot, "collected", self.cog.bot.data))
        except Exception:
            logger.exception("Failed to save collected data after user collect")

        try:
            removed = invalidate_user_puzzle_cache(self.puzzle, uid)
            logger.debug("Invalidated %s preview cache files for %s/%s", removed, self.puzzle, uid)
        except Exception:
            logger.exception("Failed to invalidate preview cache for %s/%s", self.puzzle, uid)

        try:
            puzzles_map = getattr(self.cog.bot, "data", {}) or {}
            puzzle_cfg = puzzles_map.get(self.puzzle, {}) or {}
            display_name = puzzle_cfg.get("display_name", self.puzzle)

            puzzle_folder = os.path.join(os.getcwd(), "puzzles", self.puzzle)
            if not os.path.isdir(puzzle_folder):
                alt = os.path.join(os.getcwd(), "puzzles", display_name)
                if os.path.isdir(alt):
                    puzzle_folder = alt

            rows = int(puzzle_cfg.get("rows") or puzzle_cfg.get("r") or 4)
            cols = int(puzzle_cfg.get("cols") or puzzle_cfg.get("c") or 4)

            owned = list(self.cog.bot.collected.get("user_pieces", {}).get(uid, {}).get(self.puzzle, []))
            owned = [str(x) for x in owned]

            cache_path = preview_cache_path(self.puzzle, uid, owned)
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)

            render_progress_image(
                puzzle_folder=puzzle_folder,
                collected_piece_ids=owned,
                rows=rows,
                cols=cols,
                puzzle_config=puzzle_cfg,
                output_path=cache_path,
            )
        except Exception:
            logger.exception("Pre-warm render failed for %s/%s", self.puzzle, uid)

        self.claimants.append(user)
        await interaction.response.send_message(f"✅ You collected piece `{self.piece_idx}` for **{self.puzzle}**!", ephemeral=True)

        if len(self.claimants) >= self.limit:
            try:
                for child in self.children:
                    try:
                        child.disabled = True
                    except Exception:
                        logger.exception("Failed to disable a view child")
                try:
                    self.stop()
                except Exception:
                    logger.exception("Failed to stop view")

                try:
                    await interaction.message.edit(content=f"🧩 Drop ended — all {self.limit} claims taken!", view=self)
                except Exception:
                    try:
                        await interaction.message.edit(content=f"🧩 Drop ended — all {self.limit} claims taken!", view=None)
                    except Exception:
                        logger.exception("Failed to edit drop message after completion")
            except Exception:
                logger.exception("Error completing drop UI update")

            mentions = ", ".join(u.mention for u in self.claimants)
            summary = f"🧩 Piece `{self.piece_idx}` from **{self.puzzle}** claimed by: {mentions}"
            try:
                await interaction.followup.send(content=summary, ephemeral=False)
            except Exception:
                logger.exception("Failed to send followup summary on drop completion")

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
                    logger.exception("Failed to send drop log to channel %s", getattr(self.log_channel, "id", None))

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
            slug = cfg.get("puzzle")
            if not slug:
                logger.warning("Drop config missing puzzle for channel %s", getattr(channel, "id", "unknown"))
                return False

            meta = getattr(self.bot, "data", {}).get("puzzles", {}).get(slug)
            if meta is None:
                logger.warning("Puzzle slug not found in puzzles data: %s", slug)
                return False

            display = (meta or {}).get("display_name", slug.replace("_", " ").title())

            pieces_map = getattr(self.bot, "data", {}).get("pieces", {}).get(slug, {}) or {}
            if not pieces_map:
                logger.warning("No pieces configured for puzzle %s", slug)
                return False
            # pick a random piece that isn't obviously already exhausted (you can add bookkeeping)
            piece_idx = random.choice(list(pieces_map.keys()))
            piece_file = pieces_map.get(str(piece_idx))

            embed = discord.Embed(
                title=f"🧩 {display}",
                description=f"Collect a piece of **{display}**",
                color=discord.Color.purple()
            )

            file = None
            if piece_file:
                piece_path = Path(piece_file)
                if not piece_path.is_absolute():
                    piece_path = BASE_DIR.joinpath(piece_file)
                if piece_path.exists():
                    try:
                        clean_name = f"{slug}_{piece_idx}.png"
                        logger.debug("Resolved piece path: %s", piece_path)
                        logger.debug("Using attachment filename: %s", clean_name)
                        file = discord.File(str(piece_path), filename=clean_name)
                        embed.set_image(url=f"attachment://{clean_name}")  # ← this is the missing line
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

            view = DropView(slug, str(piece_idx), limit, self.bot.get_channel(LOG_CHANNEL_ID) if LOG_CHANNEL_ID else None, self)
            if file:
                await channel.send(embed=embed, file=file, view=view)
            else:
                await channel.send(embed=embed, view=view)
            logger.info("Spawned drop for '%s' in channel %s", slug, channel.id)
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

                        slug = cfg.get("puzzle")
                        meta = getattr(self.bot, "data", {}).get("puzzles", {}).get(slug)
                        if meta is None:
                            logger.warning("Configured puzzle slug not found for channel %s: %s", ch_str, slug)
                            continue

                        value = int(cfg.get("value", 1) or 1)
                        chance = min(100, max(1, value))
                        roll = random.randint(1, 100)
                        if roll <= chance:
                            await self._spawn_drop_for_channel(channel, cfg)
                        else:
                            logger.debug("Chance miss for %s in %s (%s <= %s?)", slug, ch_str, roll, chance)

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

        ch_id = str(message.channel.id)
        cfg = self.bot.data.get("drop_channels", {}).get(ch_id)
        if not cfg or cfg.get("mode") != "messages":
            return

        cfg["message_count"] = cfg.get("message_count", 0) + 1
        if cfg["message_count"] >= cfg.get("next_trigger", 10):
            success = await self._spawn_drop_for_channel(message.channel, cfg)
            if success:
                low, high = cfg.get("value", [5, 15])
                cfg["message_count"] = 0
                cfg["next_trigger"] = random.randint(low, high)

                logger.debug(
                    "📊 Message pacing for channel %s: %s / %s",
                    ch_id,
                    cfg["message_count"],
                    cfg["next_trigger"])

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

