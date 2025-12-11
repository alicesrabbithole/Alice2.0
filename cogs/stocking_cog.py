# StockingCog with buildable assemblies (snowman example)
# - Persists per-user stickers/buildables to data/stockings.json
# - Loads sticker/buildable defs from data/stickers.json and data/buildables.json
# - API: award_sticker(user_id, sticker_key, channel) and award_part(user_id, buildable_key, part_key, channel)
# - Command: /stocking show, /stickgive, /partgive, /stickadd, /buildable_add

import asyncio
import json
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

import discord
from discord.ext import commands

# data paths
DATA_DIR = Path("data")
STOCKINGS_FILE = DATA_DIR / "stockings.json"
STICKERS_DEF_FILE = DATA_DIR / "stickers.json"
BUILDABLES_DEF_FILE = DATA_DIR / "buildables.json"
ASSETS_DIR = DATA_DIR / "stocking_assets"

DEFAULT_CAPACITY = 12
AUTO_ROLE_ID: Optional[int] = None  # set to role id if you want automatic role awarding

_lock = asyncio.Lock()


def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)


# NOTE: removed `name="Stockings"` so the cog registers as "StockingCog"
class StockingCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        ensure_dirs()
        self._data: Dict[str, Dict[str, Any]] = {}
        self._stickers_def: Dict[str, Dict[str, Any]] = {}
        self._buildables_def: Dict[str, Dict[str, Any]] = {}
        self._load_all()

    def _load_all(self) -> None:
        try:
            if STOCKINGS_FILE.exists():
                with STOCKINGS_FILE.open("r", encoding="utf-8") as fh:
                    self._data = json.load(fh)
            else:
                self._data = {}
        except Exception:
            self._data = {}

        try:
            if STICKERS_DEF_FILE.exists():
                with STICKERS_DEF_FILE.open("r", encoding="utf-8") as fh:
                    self._stickers_def = json.load(fh) or {}
            else:
                self._stickers_def = {}
        except Exception:
            self._stickers_def = {}

        try:
            if BUILDABLES_DEF_FILE.exists():
                with BUILDABLES_DEF_FILE.open("r", encoding="utf-8") as fh:
                    self._buildables_def = json.load(fh) or {}
            else:
                # write a sensible default if missing
                self._buildables_def = {
                    "snowman": {
                        "base": "buildables/snowman/base.png",
                        "parts": {
                            "carrot": {"file": "buildables/snowman/parts/carrot.png", "offset": [350, 170], "z": 10},
                            "hat": {"file": "buildables/snowman/parts/hat.png", "offset": [210, 60], "z": 30},
                            "scarf": {"file": "buildables/snowman/parts/scarf.png", "offset": [170, 240], "z": 20}
                        },
                        "capacity_slots": 3,
                        "role_on_complete": None
                    }
                }
                with BUILDABLES_DEF_FILE.open("w", encoding="utf-8") as fh:
                    json.dump(self._buildables_def, fh, ensure_ascii=False, indent=2)
        except Exception:
            self._buildables_def = {}

    async def _save(self) -> None:
        async with _lock:
            try:
                STOCKINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
                with STOCKINGS_FILE.open("w", encoding="utf-8") as fh:
                    json.dump(self._data, fh, ensure_ascii=False, indent=2)
            except Exception:
                pass

    # user data helpers
    def _ensure_user(self, user_id: int) -> Dict[str, Any]:
        key = str(user_id)
        if key not in self._data:
            self._data[key] = {"stickers": [], "capacity": DEFAULT_CAPACITY, "buildables": {}}
        return self._data[key]

    def get_user_stocking(self, user_id: int) -> Dict[str, Any]:
        return self._ensure_user(user_id)

    # legacy sticker awarding
    async def award_sticker(self, user_id: int, sticker_key: str, channel: Optional[discord.TextChannel] = None) -> bool:
        if sticker_key not in self._stickers_def:
            return False
        user = self._ensure_user(user_id)
        user.setdefault("stickers", []).append(sticker_key)
        await self._save()
        if channel:
            try:
                member = channel.guild.get_member(user_id)
                mention = member.mention if member else f"<@{user_id}>"
                await channel.send(f"🎉 {mention} earned a **{sticker_key}** sticker! Use `/stocking show` to view it.")
            except Exception:
                pass
        await self._maybe_award_role(user_id, channel.guild if channel is not None else None)
        return True

    # buildables (parts)
    async def award_part(self, user_id: int, buildable_key: str, part_key: str, channel: Optional[discord.TextChannel] = None) -> bool:
        build_def = self._buildables_def.get(buildable_key)
        if not build_def:
            return False
        parts_def = build_def.get("parts", {})
        if part_key not in parts_def:
            return False

        user = self._ensure_user(user_id)
        b = user.setdefault("buildables", {}).setdefault(buildable_key, {"parts": [], "completed": False})

        # distinct parts only
        if part_key in b["parts"]:
            if channel:
                try:
                    member = channel.guild.get_member(user_id)
                    mention = member.mention if member else f"<@{user_id}>"
                    await channel.send(f"{mention} already has the **{part_key}** for {buildable_key}.")
                except Exception:
                    pass
            return False

        b["parts"].append(part_key)
        await self._save()

        if channel:
            try:
                member = channel.guild.get_member(user_id)
                mention = member.mention if member else f"<@{user_id}>"
                await channel.send(f"🎉 {mention} received the **{part_key}** for **{buildable_key}**! Use `/stocking show` to view progress.")
            except Exception:
                pass

        capacity_slots = int(build_def.get("capacity_slots", len(parts_def)))
        total = len(b.get("parts", []))
        if total >= capacity_slots or total >= len(parts_def):
            b["completed"] = True
            await self._save()
            role_id = build_def.get("role_on_complete") or AUTO_ROLE_ID
            if role_id and channel and channel.guild:
                try:
                    role = channel.guild.get_role(int(role_id))
                    member = channel.guild.get_member(user_id)
                    if role and member and role not in member.roles:
                        await member.add_roles(role, reason=f"{buildable_key} completed")
                        await channel.send(f"🎉 {member.mention} has completed **{buildable_key}** and was given the role {role.mention}!")
                except Exception:
                    pass
        return True

    # render buildable composite
    async def render_buildable(self, user_id: int, buildable_key: str) -> Optional[Path]:
        try:
            from PIL import Image
        except Exception:
            return None

        build_def = self._buildables_def.get(buildable_key)
        if not build_def:
            return None
        base_rel = build_def.get("base")
        if not base_rel:
            return None
        base_path = ASSETS_DIR / base_rel
        if not base_path.exists():
            return None
        try:
            base = Image.open(base_path).convert("RGBA")
        except Exception:
            return None

        user = self._ensure_user(user_id)
        ub = user.get("buildables", {}).get(buildable_key, {"parts": []})
        user_parts = ub.get("parts", [])

        part_items: List[Tuple[int, Image.Image, Tuple[int, int]]] = []
        for pkey in user_parts:
            pdef = build_def.get("parts", {}).get(pkey)
            if not pdef:
                continue
            ppath = ASSETS_DIR / pdef.get("file", "")
            if not ppath.exists():
                continue
            try:
                img = Image.open(ppath).convert("RGBA")
                offset = tuple(pdef.get("offset", [0, 0]))
                z = int(pdef.get("z", 0))
                part_items.append((z, img, offset))
            except Exception:
                continue

        part_items.sort(key=lambda t: t[0])
        for (_z, img, (ox, oy)) in part_items:
            try:
                base.paste(img, (int(ox), int(oy)), img)
            except Exception:
                try:
                    w, h = base.size
                    px = max(0, min(w - 1, int(ox)))
                    py = max(0, min(h - 1, int(oy)))
                    base.paste(img, (px, py), img)
                except Exception:
                    continue

        out = ASSETS_DIR / f"{buildable_key}_user_{user_id}.png"
        try:
            base.save(out, format="PNG")
            return out
        except Exception:
            return None

    # legacy fullness role awarding
    async def _maybe_award_role(self, user_id: int, guild: Optional[discord.Guild]) -> None:
        if AUTO_ROLE_ID is None or guild is None:
            return
        user = self._ensure_user(user_id)
        total = len(user.get("stickers", []))
        capacity = int(user.get("capacity", DEFAULT_CAPACITY))
        if total >= capacity:
            try:
                role = guild.get_role(AUTO_ROLE_ID)
                member = guild.get_member(user_id)
                if role and member and role not in member.roles:
                    await member.add_roles(role, reason="Sticker capacity reached")
                    try:
                        chan = guild.system_channel
                        if chan:
                            await chan.send(f"{member.mention} filled their sticker capacity and was awarded {role.mention}!")
                    except Exception:
                        pass
            except Exception:
                pass

    # commands
    async def _ephemeral_reply(self, ctx: commands.Context, content: str) -> None:
        try:
            if getattr(ctx, "interaction", None) is not None and getattr(ctx.interaction, "response", None) is not None:
                if not ctx.interaction.response.is_done():
                    await ctx.interaction.response.send_message(content, ephemeral=True)
                    return
        except Exception:
            pass
        await ctx.reply(content, mention_author=False)

    @commands.hybrid_command(name="stocking", description="Show your stocking (stickers and buildables).")
    @commands.guild_only()
    async def stocking_show(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        target = member or ctx.author
        user = self._ensure_user(target.id)
        buildables = user.get("buildables", {})
        any_sent = False
        for bkey in buildables.keys():
            path = await self.render_buildable(target.id, bkey)
            if path and path.exists():
                try:
                    await ctx.reply(file=discord.File(path), mention_author=False)
                    any_sent = True
                except Exception:
                    continue
        stickers = user.get("stickers", [])
        if stickers:
            await ctx.reply(f"{target.display_name}'s stickers: " + ", ".join(stickers), mention_author=False)
            any_sent = True
        if not any_sent:
            await ctx.reply(f"{target.display_name} has no stickers or buildables yet.", mention_author=False)

    @commands.hybrid_command(name="stickgive", description="Give a sticker to a user (staff only).")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    async def stick_give(self, ctx: commands.Context, member: discord.Member, sticker_key: str):
        sticker_key = sticker_key.lower()
        if sticker_key not in self._stickers_def:
            await self._ephemeral_reply(ctx, f"Unknown sticker key: {sticker_key}. Known: {', '.join(self._stickers_def.keys())}")
            return
        awarded = await self.award_sticker(member.id, sticker_key, ctx.channel)
        if awarded:
            await self._ephemeral_reply(ctx, f"✅ Awarded {sticker_key} to {member.mention}.")
        else:
            await self._ephemeral_reply(ctx, "❌ Failed to award sticker.")

    @commands.hybrid_command(name="partgive", description="Give a buildable part to a user (staff only).")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    async def part_give(self, ctx: commands.Context, member: discord.Member, buildable_key: str, part_key: str):
        buildable_key = buildable_key.lower()
        part_key = part_key.lower()
        awarded = await self.award_part(member.id, buildable_key, part_key, ctx.channel)
        if awarded:
            await self._ephemeral_reply(ctx, f"✅ Awarded part `{part_key}` for `{buildable_key}` to {member.mention}.")
        else:
            await self._ephemeral_reply(ctx, "❌ Failed to award part (unknown buildable/part or user already has part).")

    @commands.hybrid_command(name="stickadd", description="Add or update a sticker definition (admin).")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def stick_add(self, ctx: commands.Context, key: str, filename: str):
        key = key.lower()
        self._stickers_def[key] = {"file": filename}
        try:
            with STICKERS_DEF_FILE.open("w", encoding="utf-8") as fh:
                json.dump(self._stickers_def, fh, ensure_ascii=False, indent=2)
            await self._ephemeral_reply(ctx, f"✅ Sticker {key} -> {filename} saved. Put the file in `{ASSETS_DIR}`.")
        except Exception as exc:
            await self._ephemeral_reply(ctx, f"❌ Failed to save sticker definition: {exc}")

    @commands.hybrid_command(name="buildable_add", description="Add/update buildable definition (admin).")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def buildable_add(self, ctx: commands.Context):
        await self._ephemeral_reply(ctx, f"Edit `{BUILDABLES_DEF_FILE}` and place assets under `{ASSETS_DIR}`. Then reload the cog.")

    async def cog_unload(self):
        await self._save()


async def setup(bot: commands.Bot):
    await bot.add_cog(StockingCog(bot))