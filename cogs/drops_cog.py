import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional
import random
import logging
from datetime import datetime
from cogs.db_utils import puzzle_autocomplete_shared, resolve_puzzle_key
from cogs.drop_runtime_cog import DropView

LOG_CHANNEL_ID = 1411859714144468992
logger = logging.getLogger(__name__)

# --- Autocomplete proxies ---
async def puzzle_autocomplete_proxy(interaction: discord.Interaction, current: str):
    cog = interaction.client.get_cog("DropRuntime")
    return await cog.puzzle_autocomplete(interaction, current) if cog else []

async def piece_autocomplete_proxy(interaction: discord.Interaction, current: str):
    cog = interaction.client.get_cog("DropRuntime")
    return await cog.piece_autocomplete(interaction, current) if cog else []

class DropsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.recent_claims = []

    def resolve_puzzle_key(self, requested: str) -> Optional[str]:
        requested = requested.lower().strip()
        for key in self.bot.data.get("puzzles", {}):
            if requested == key.lower():
                return key
        return None

    def _resolve_requested_from_channel(self, channel: discord.TextChannel) -> str:
        return channel.name.lower()

    def build_drop_embed(self, puzzle_key: str, piece_file: Optional[str], filename_override: str = None) -> tuple[
        discord.Embed, discord.File | None]:
        display = self.bot.data.get("puzzles", {}).get(puzzle_key, {}).get("display_name",
                                                                           puzzle_key.replace("_", " ").title())
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
                filename = filename_override or piece_path.name
                file = discord.File(str(piece_path), filename=filename)
                embed.set_image(url=f"attachment://{filename}")

        return embed, file

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
        # ✅ Resolve display name to canonical slug
        canonical = resolve_puzzle_key(self.bot, puzzle)
        if not canonical:
            await ctx.reply(f"❌ Puzzle not found: `{puzzle}`", ephemeral=True)
            return

        ch_id = str(channel.id)
        self.bot.data["drop_channels"][ch_id] = {
            "puzzle": canonical,
            "mode": mode,
            "value": [5, 15],  # ✅ message range
            "claims_range": [1, 3],
            "message_count": 0,
            "next_trigger": random.randint(5, 15)
        }

        puzzle_meta = self.bot.data.get("puzzles", {}).get(canonical, {})
        display_name = puzzle_meta.get("display_name", canonical)

        await ctx.reply(
            f"✅ {channel.mention} configured.\n"
            f"Puzzle: {display_name} | Mode: {mode} | Value: {value} | Claims: 1–3",
            mention_author=False
        )

    @commands.command()
    async def testdrop(self, ctx):
        slug = normalize_puzzle_identifier(self.bot, "Alice Test")
        if not slug:
            await ctx.send("❌ Puzzle not found.")
            return

        await self._spawn_drop_for_channel(ctx.channel, {
            "puzzle": slug,
            "claims_range": [1, 3]
        })

    @commands.hybrid_command(name="testdrop", description="Force a test drop (admin only)")
    @commands.is_owner()
    @app_commands.autocomplete(puzzle=puzzle_autocomplete_proxy, piece=piece_autocomplete_proxy)
    async def testdrop(self, ctx: commands.Context, channel: discord.TextChannel = None, puzzle: str = None, piece: str = None):
        if getattr(ctx, "interaction", None):
            try:
                await ctx.defer(ephemeral=True)
            except Exception:
                pass

        ch = channel or ctx.channel
        requested = puzzle or self._resolve_requested_from_channel(ch)
        puzzle_key = self.resolve_puzzle_key(requested)
        if not puzzle_key:
            await ctx.reply(f"No puzzle found for `{requested}`.", mention_author=False)
            return

        pieces_map = self.bot.data.get("pieces", {}).get(puzzle_key, {}) or {}
        if not pieces_map:
            await ctx.reply(f"No pieces configured for puzzle **{puzzle_key}**.", mention_author=False)
            return

        piece_idx = str(piece) if piece else ("1" if "1" in pieces_map else next(iter(pieces_map.keys())))
        piece_file = pieces_map.get(piece_idx)
        embed, file = self.build_drop_embed(puzzle_key, piece_file)


        chan_cfg = self.bot.collected.get("drop_channels", {}).get(str(ch.id), {}) or {}
        raw_range = chan_cfg.get("claims_range") or [1, 3]
        try:
            low, high = int(raw_range[0]), int(raw_range[1])
            if low > high:
                low, high = high, low
        except Exception:
            low, high = 1, 3
        limit = random.randint(low, high)

        log_channel = self.bot.get_channel(LOG_CHANNEL_ID)
        view = DropView(puzzle_key, piece_idx, limit, log_channel, self)

        try:
            if file:
                sent_msg = await ch.send(embed=embed, file=file, view=view)
            else:
                sent_msg = await ch.send(embed=embed, view=view)
            if getattr(ctx, "interaction", None):
                await ctx.interaction.followup.send(f"🧩 Drop tested: puzzle={puzzle_key} piece={piece_idx} limit={limit} sent in {ch.mention}", ephemeral=True)
            else:
                await ctx.reply(f"🧩 Drop tested: puzzle={puzzle_key} piece={piece_idx} limit={limit} sent in {ch.mention}", mention_author=False)
        except Exception:
            try:
                if getattr(ctx, "interaction", None):
                    await ctx.interaction.followup.send(f"Failed to send test drop in {ch.mention}", ephemeral=True)
                else:
                    await ctx.reply(f"Failed to send test drop in {ch.mention}", mention_author=False)
            except Exception:
                pass

    @commands.command(name="drop")
    @commands.is_owner()
    async def drop(self, ctx: commands.Context, puzzle_key: str, piece_idx: Optional[str] = None):
        canonical = self.resolve_puzzle_key(puzzle_key)
        if not canonical:
            await ctx.send(f"No puzzle found for `{puzzle_key}`.")
            return

        pieces_map = self.bot.data.get("pieces", {}).get(canonical, {}) or {}
        if not pieces_map:
            await ctx.send(f"No pieces configured for puzzle **{canonical}**.")
            return

        piece_idx = piece_idx or ("1" if "1" in pieces_map else next(iter(pieces_map.keys())))
        chan_settings = self.bot.collected.get("drop_channels", {}).get(str(ctx.channel.id))
        if not chan_settings:
            await ctx.send("⚠️ This channel is not configured as a drop channel.")
            return

        claims_range = chan_settings.get("claims_range", [1, 3])
        limit = random.randint(claims_range[0], claims_range[1])

        log_channel = self.bot.get_channel(LOG_CHANNEL_ID)
        view = DropView(canonical, str(piece_idx), limit, log_channel, self)

        await ctx.send(f"🧩 A piece has dropped: **{piece_idx}** from **{canonical}** — First {limit} collectors win!", view=view)

    @commands.command(name="dbg_puzzles")
    @commands.is_owner()
    async def dbg_puzzles(self, ctx: commands.Context):
        puzzles = getattr(self.bot, "data", {}).get("puzzles", {})
        collected = getattr(self.bot, "collected", {})
        await ctx.reply("puzzles keys: {}\ndrop_channels: {}".format(list(puzzles.keys()), collected.get("drop_channels")))

async def setup(bot: commands.Bot):
    await bot.add_cog(DropsCog(bot))