# StockingCog with buildable assemblies (snowman example)
# - Persists per-user stickers/buildables to data/stockings.json
# - Loads sticker/buildable defs from data/stickers.json and data/buildables.json
# - API: award_sticker(user_id, sticker_key, channel) and award_part(user_id, buildable_key, part_key, channel, announce=True)
# - Command: /stocking show, /stickgive, /partgive, /stickadd, /buildable_add
# - /stocking_gallery view to flip through per-user buildables

import asyncio
import json
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

import discord
from discord import app_commands
from discord.ext import commands
from ui.stocking_render_helpers import render_stocking_image_auto

# data paths
DATA_DIR = Path("data")
STOCKINGS_FILE = DATA_DIR / "stockings.json"
STICKERS_DEF_FILE = DATA_DIR / "stickers.json"
BUILDABLES_DEF_FILE = DATA_DIR / "buildables.json"
ASSETS_DIR = DATA_DIR / "stocking_assets"

DEFAULT_CAPACITY = 12
# Global fallback reward role (set to your ID or None)
AUTO_ROLE_ID: Optional[int] = 1448857904282206208

_lock = asyncio.Lock()

# Part embed colors (used when StockingCog announces)
PART_COLORS = {
    "carrot": discord.Color.from_rgb(255, 165, 0),      # orange
    "eyes": discord.Color.from_rgb(158, 158, 158),      # gray
    "mouth": discord.Color.from_rgb(158, 158, 158),     # gray
    "buttons": discord.Color.from_rgb(158, 158, 158),   # gray
    "scarf": discord.Color.from_rgb(220, 20, 60),       # red
    "hat": discord.Color.from_rgb(0, 0, 128),           # navy
}
DEFAULT_PART_COLOR = discord.Color.from_rgb(47, 49, 54)

def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)


async def _autocomplete_sticker(interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
    try:
        if STICKERS_DEF_FILE.exists():
            with STICKERS_DEF_FILE.open("r", encoding="utf-8") as fh:
                stickers = json.load(fh) or {}
        else:
            stickers = {}
    except Exception:
        stickers = {}

    cur = (current or "").strip().lower()
    choices: List[app_commands.Choice[str]] = []
    for key in sorted(stickers.keys()):
        if not cur or key.lower().startswith(cur):
            choices.append(app_commands.Choice(name=key, value=key))
            if len(choices) >= 25:
                break
    return choices


class StockingCog(commands.Cog, name="StockingCog"):
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
                # write sensible default if missing
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

    # sticker awarding
    async def award_sticker(self, user_id: int, sticker_key: str, channel: Optional[discord.TextChannel] = None, *, announce: bool = True) -> bool:
        if sticker_key not in self._stickers_def:
            return False
        user = self._ensure_user(user_id)
        user.setdefault("stickers", []).append(sticker_key)
        await self._save()
        if announce and channel:
            try:
                member = channel.guild.get_member(user_id) if channel and channel.guild else None
                mention = member.mention if member else f"<@{user_id}>"
                await channel.send(f"🎉 {mention} earned a **{sticker_key}** sticker! Use `/stocking show` to view it.")
            except Exception:
                pass
        await self._maybe_award_role(user_id, channel.guild if channel is not None else None)
        return True

    # buildables (parts) — announce flag controls whether StockingCog posts the channel message.
    async def award_part(self, user_id: int, buildable_key: str, part_key: str, channel: Optional[discord.TextChannel] = None, *, announce: bool = True) -> bool:
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
            if announce and channel:
                try:
                    member = channel.guild.get_member(user_id) if channel and channel.guild else None
                    mention = member.mention if member else f"<@{user_id}>"
                    await channel.send(f"{mention} already has the **{part_key}** for {buildable_key}.")
                except Exception:
                    pass
            return False

        b["parts"].append(part_key)
        await self._save()

        # regenerate composite (writes file to assets dir)
        out_path = None
        try:
            out_path = await self.render_buildable(user_id, buildable_key)
        except Exception:
            out_path = None

        # If announce=True, StockingCog posts an embed + image. If False, caller (listener) will handle announcement.
        if announce and channel:
            mention = f"<@{user_id}>"
            try:
                member = channel.guild.get_member(user_id) if channel and channel.guild else None
                if member:
                    mention = member.mention
            except Exception:
                pass

            color = PART_COLORS.get(part_key.lower(), DEFAULT_PART_COLOR)
            emb = discord.Embed(
                title=f"Part Awarded — {buildable_key}",
                description=f"🎉 {mention} received the **{part_key}** for **{buildable_key}**!",
                color=color,
            )
            try:
                if out_path and out_path.exists():
                    file = discord.File(out_path, filename=out_path.name)
                    emb.set_image(url=f"attachment://{out_path.name}")
                    await channel.send(embed=emb, file=file)
                else:
                    await channel.send(embed=emb)
            except Exception:
                try:
                    await channel.send(f"🎉 {mention} received the **{part_key}** for **{buildable_key}**!")
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
                        try:
                            comp_emb = discord.Embed(
                                title=f"{buildable_key} Completed!",
                                description=f"🎉 {member.mention} has completed **{buildable_key}** and was given the role {role.mention}!",
                                color=discord.Color.green(),
                            )
                            await channel.send(embed=comp_emb)
                        except Exception:
                            try:
                                await channel.send(f"🎉 {member.mention} has completed **{buildable_key}** and was given the role {role.mention}!")
                            except Exception:
                                pass
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

        part_items: List[Tuple[int, "Image.Image", Tuple[int, int]]] = []
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

    # inside StockingCog class — add this async wrapper method
    async def _render_user_stocking(self, user_id: int) -> Optional[Path]:
        """
        Async wrapper that calls the blocking render_stocking_image_auto in a thread pool.
        Returns Path to generated image or None.
        """
        loop = asyncio.get_running_loop()
        user_stickers = self._ensure_user(user_id).get("stickers", [])
        stickers_def = self._stickers_def
        # run the blocking work in default executor
        try:
            out = await loop.run_in_executor(
                None,
                render_stocking_image_auto,
                user_id,
                user_stickers,
                stickers_def,
                ASSETS_DIR,
                "template.png",  # template should be ASSETS_DIR/template.png (or change)
                4,  # cols
                3,  # rows
                f"stocking_{user_id}.png"
            )
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

    # commands and gallery omitted here for brevity; preserve your existing commands in your file
    async def _ephemeral_reply(self, ctx: commands.Context, content: str) -> None:
        try:
            if getattr(ctx, "interaction", None) is not None and getattr(ctx.interaction, "response", None) is not None:
                if not ctx.interaction.response.is_done():
                    await ctx.interaction.response.send_message(content, ephemeral=True)
                    return
        except Exception:
            pass
        await ctx.reply(content, mention_author=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(StockingCog(bot))