import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional
import random
import logging

logger = logging.getLogger(__name__)
from datetime import datetime
from cogs.db_utils import puzzle_autocomplete_shared, resolve_puzzle_key, normalize_puzzle_identifier
import cogs.db_utils as db_utils
from cogs.db_utils import load_data
from cogs.drop_runtime_cog import DropView
from cogs.log_utils import log, log_exception
from pathlib import Path
from cogs.constants import BASE_DIR
from quick_test_preview import puzzle_key
from typing import Any
from cogs.drop_config import DropConfig

logger.warning("🧪 [COG NAME] loaded")

LOG_CHANNEL_ID = 1411859714144468992


# --- Autocomplete proxies ---
async def puzzle_autocomplete_proxy(interaction: discord.Interaction, current: str):
    cog = interaction.client.get_cog("DropRuntime")
    return await cog.puzzle_autocomplete(interaction, current) if cog else []

async def piece_autocomplete_proxy(interaction: discord.Interaction, current: str):
    cog = interaction.client.get_cog("DropRuntime")
    return await cog.piece_autocomplete(interaction, current) if cog else []

from cogs.db_utils import slugify_key

def normalize_config(bot):
    data = bot.data
    if "pieces" in data:
        data["pieces"] = {slugify_key(k): v for k, v in data["pieces"].items()}
    if "puzzles" in data:
        data["puzzles"] = {slugify_key(k): v for k, v in data["puzzles"].items()}
    bot.data = data

class DropsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.recent_claims: list[dict[str, Any]] = []
        normalize_config(bot)

        def get_claim_limit(self) -> int:
            try:
                low, high = int(self.claims_range[0]), int(self.claims_range[1])
                if low > high:
                    low, high = high, low
                return random.randint(low, high)
            except Exception:
                return random.randint(1, 3)

    def resolve_puzzle_key(self, requested: str) -> Optional[str]:
        requested = requested.lower().strip()
        for key in self.bot.data.get("puzzles", {}):
            if requested == key.lower():
                return key
        return None

    def _resolve_requested_from_channel(self, channel: discord.TextChannel) -> str:
        return channel.name.lower()

    logger.info("🧪 drops_cog.py loaded — build_drop_embed is defined")
    def build_drop_embed(self, puzzle_key: str, piece_file: Optional[str], filename_override: str = None) -> tuple[
        discord.Embed, discord.File | None]:
        display = self.bot.data.get("puzzles", {}).get(puzzle_key, {}).get("display_name", puzzle_key.replace("_", " ").title())
        embed = discord.Embed(
            title=f"🧩 {display}",
            description=f"Collect a piece of **{display}**",
            color=discord.Color.purple()
        )
        logger.info("🧪 build_drop_embed: piece_file = %s", piece_file)

        file = None
        if piece_file:
            piece_path = Path(piece_file)
            if not piece_path.is_absolute():
                piece_path = BASE_DIR.joinpath(piece_file)
            if piece_path.exists():
                filename = filename_override or piece_path.name
                file = discord.File(str(piece_path), filename=filename)
                embed.set_image(url=f"attachment://{filename}")

        return embed, file

    async def _spawn_drop_for_channel(self, channel: discord.TextChannel, chan_cfg: Optional[dict] = None):
        # Step 1: Resolve raw puzzle key
        if chan_cfg and "puzzle" in chan_cfg:
            raw_key = chan_cfg["puzzle"]
            logger.info("Drop config provided for channel %s: puzzle=%s", channel.id, raw_key)
        else:
            raw_key = self.bot.data.get("default_puzzle", "alice_test")
            logger.info("No config provided; using default puzzle: %s", raw_key)

        # Step 2: Build DropConfig
        config = DropConfig(self.bot, channel.id, chan_cfg or {"puzzle": raw_key})
        logger.info("Slugified puzzle key: %s", config.slug)
        logger.info("Available piece keys: %s", list(self.bot.data.get("pieces", {}).keys()))

        # Step 3: Get pieces
        pieces_map = config.pieces_map
        if not pieces_map:
            logger.warning("No pieces configured for puzzle %r", config.slug)
            return

        piece_idx = "1" if "1" in pieces_map else next(iter(pieces_map.keys()))
        piece_file = pieces_map.get(piece_idx)
        embed, file = self.build_drop_embed(config.slug, piece_file)

        # Step 4: Claims range
        limit = config.get_claim_limit()
        await log(self.bot, f"📤 Drop: puzzle={config.slug} piece={piece_idx} limit={limit} in #{channel.name}")

        # Step 5: Send drop
        log_channel = self.bot.get_channel(LOG_CHANNEL_ID)
        view = DropView(
            cog=self,
            puzzle=config.slug,
            piece_idx=int(piece_idx),
            limit=limit,
            log_channel=log_channel
        )
        try:
            if file:
                await channel.send(embed=embed, file=file, view=view)
            else:
                await channel.send(embed=embed, view=view)
            logger.info("Drop sent to channel %s for puzzle %s", channel.id, config.slug)
        except Exception:
            logger.exception("Failed to send drop to channel %s", channel.id)

    @commands.hybrid_command(
        name="setdrop",
        description="Configure a drop channel",
        extras={"category": "Puzzles", "Admin": True}
    )
    @app_commands.autocomplete(puzzle=puzzle_autocomplete_shared)
    @app_commands.describe(
        channel="Channel to configure",
        puzzle="Puzzle name (e.g. Alice Test)",
        mode="Drop trigger mode",
        value="Threshold value for triggering drops"
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="messages", value="messages"),
            app_commands.Choice(name="timer", value="timer")
        ]
    )
    @commands.has_permissions(administrator=True)
    async def setdrop(
            self,
            ctx: commands.Context,
            channel: discord.TextChannel,
            puzzle: str,
            mode: str,
            value: int = 10
    ):
        canonical = resolve_puzzle_key(self.bot, puzzle)
        if not canonical:
            await ctx.reply(f"❌ Puzzle not found: `{puzzle}`", ephemeral=True)
            return

        piece_id = value
        view = DropView(cog=self, puzzle=canonical, piece_idx=piece_id, limit=1, log_channel=channel)
        view.message = await channel.send("🧩 Puzzle drop!", view=view)

        ch_id = str(channel.id)
        self.bot.data["drop_channels"][ch_id] = {
            "puzzle": canonical,
            "mode": mode,
            "value": [max(1, value - 5), value + 5],
            "claims_range": [1, 3],
            "message_count": 0,
            "next_trigger": random.randint(5, 15)
        }

        display_name = self.bot.data.get("puzzles", {}).get(canonical, {}).get("display_name", canonical)
        await ctx.reply(
            f"✅ {channel.mention} configured.\n"
            f"Puzzle: {display_name} | Mode: {mode} | Value: {value} | Claims: 1–3",
            mention_author=False
        )

    @commands.command(name="spawn", help="Spawn a puzzle piece drop into the current channel")
    @commands.has_permissions(administrator=True)
    async def spawn(self, ctx: commands.Context, puzzle: str, piece: int = None):
        puzzle_key = resolve_puzzle_key(self.bot, puzzle)
        if not puzzle_key:
            await ctx.send(f"❌ Puzzle not found: `{puzzle}`")
            return

        puzzle_cfg = self.bot.data.get("puzzles", {}).get(puzzle_key, {})
        if not puzzle_cfg:
            await ctx.send(f"⚠️ Puzzle config missing for `{puzzle_key}`")
            return

        rows = puzzle_cfg.get("rows", 4)
        cols = puzzle_cfg.get("cols", 4)
        total = rows * cols
        piece_id = piece if piece else random.randint(1, total)

        view = DropView(cog=self, puzzle=puzzle_key, piece_idx=piece_id, limit=1, log_channel=ctx.channel)
        view.message = await ctx.send(
            f"🧩 A piece has spawned for **{puzzle_cfg.get('display_name', puzzle_key)}**!",
            view=view
        )

    @commands.hybrid_command(name="testdrop", description="Force a test drop (admin only)")
    @commands.is_owner()
    @app_commands.autocomplete(puzzle=puzzle_autocomplete_proxy, piece=piece_autocomplete_proxy)
    @app_commands.describe(
        puzzle="Puzzle name (e.g. Alice Test)",
        piece="Specific piece index (optional)"
    )
    async def testdrop(self, ctx: commands.Context, puzzle: str = None, piece: str = None):
        logger.info("📥 testdrop triggered by %s", ctx.author)

        if getattr(ctx, "interaction", None):
            try:
                if not ctx.interaction.response.is_done():
                    await ctx.defer(ephemeral=True)
            except Exception as e:
                logger.warning("⚠️ ctx.defer failed: %s", e)

        ch = ctx.channel
        requested = puzzle or self._resolve_requested_from_channel(ch)
        slug = db_utils.slugify_key(requested)
        data = self.bot.data
        puzzle_key = slug if slug in data.get("puzzles", {}) else None

        if not puzzle_key and requested:
            try:
                from cogs.db_utils import slugify_key, load_data, resolve_puzzle_key
                slug_attempt = slugify_key(requested)
                d = load_data()
                if slug_attempt in d.get("puzzles", {}):
                    puzzle_key = resolve_puzzle_key(self.bot, requested)
                    slug = puzzle_key
            except Exception as e:
                logger.exception("testdrop: slugify fallback failed: %s", e)

        if not puzzle_key:
            await ctx.reply(f"No puzzle found for `{requested}`.", mention_author=False)
            return

        chan_settings = data.get("drop_channels", {}).get(str(ch.id), {}) or {}
        config = DropConfig(self.bot, ch.id, {
            "puzzle": puzzle_key,
            "claims_range": chan_settings.get("claims_range", [1, 3]),
            "mode": "manual",
            "value": 100
        })

        pieces_map = config.pieces_map
        if not pieces_map:
            await ctx.reply(f"No pieces configured for puzzle **{config.slug}**.", mention_author=False)
            return

        piece_idx = str(piece) if piece else random.choice(list(pieces_map.keys()))
        piece_file = pieces_map.get(piece_idx)
        if not piece_file:
            await ctx.reply(f"Piece `{piece_idx}` not found for puzzle **{config.slug}**.", mention_author=False)
            return

        limit = config.get_claim_limit()
        log_channel = self.bot.get_channel(LOG_CHANNEL_ID)
        embed, file = self.build_drop_embed(config.slug, piece_file)
        view = DropView(cog=self, puzzle=config.slug, piece_idx=int(piece_idx), limit=limit, log_channel=log_channel)

        try:
            if file:
                view.message = await ch.send(embed=embed, file=file, view=view)
            else:
                view.message = await ch.send(embed=embed, view=view)

            msg = f"🧩 Drop tested: puzzle={config.slug} piece={piece_idx} limit={limit} sent in {ch.mention}"
            if getattr(ctx, "interaction", None):
                await ctx.interaction.followup.send(msg, ephemeral=True)
            else:
                await ctx.reply(msg, mention_author=False)

        except Exception as e:
            logger.exception("❌ Failed to send test drop: %s", e)
            try:
                fail_msg = f"Failed to send test drop in {ch.mention}"
                if getattr(ctx, "interaction", None):
                    await ctx.interaction.followup.send(fail_msg, ephemeral=True)
                else:
                    await ctx.reply(fail_msg, mention_author=False)
            except Exception as e2:
                logger.warning("⚠️ Failed to send fallback error message: %s", e2)

    @commands.command(name="dbg_puzzles")
    @commands.is_owner()
    async def dbg_puzzles(self, ctx: commands.Context):
        puzzles = self.bot.data.get("puzzles", {})
        drop_channels = self.bot.data.get("drop_channels", {})
        puzzle_keys = ", ".join(sorted(puzzles.keys()))
        channel_ids = ", ".join(drop_channels.keys())
        await ctx.reply(f"🧩 Puzzles: {puzzle_keys}\n📤 Drop Channels: {channel_ids}")

    @commands.command(name="dbg_pieces")
    @commands.is_owner()
    async def dbg_pieces(self, ctx: commands.Context, puzzle: str):
        key = db_utils.slugify_key(puzzle)
        pieces = self.bot.data.get("pieces", {}).get(key)
        display = self.bot.data.get("puzzles", {}).get(key, {}).get("display_name", key)

        if not pieces:
            await ctx.send(f"❌ No pieces found for `{display}`.")
        else:
            piece_list = ", ".join(sorted(pieces.keys()))
            await ctx.send(f"🧩 `{display}` has {len(pieces)} pieces: {piece_list}")

    @commands.command(name="leaderboard", help="Show top collectors for a puzzle")
    @commands.has_permissions(administrator=True)
    async def leaderboard(self, ctx: commands.Context, puzzle: str):
        from cogs.db_utils import get_leaderboard  # adjust path if needed

        key = db_utils.slugify_key(puzzle)
        display = self.bot.data.get("puzzles", {}).get(key, {}).get("display_name", key)
        leaderboard = get_leaderboard(key)

        if not leaderboard:
            await ctx.send(f"❌ No leaderboard data found for `{display}`.")
            return

        embed = discord.Embed(
            title=f"🏆 Leaderboard: {display}",
            description="Top collectors by piece count",
            color=discord.Color.gold()
        )

        for rank, (uid, count) in enumerate(leaderboard[:10], start=1):
            user = self.bot.get_user(int(uid))
            name = user.mention if user else f"<@{uid}>"
            embed.add_field(name=f"#{rank}", value=f"{name} — `{count}` pieces", inline=False)

        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(DropsCog(bot))
