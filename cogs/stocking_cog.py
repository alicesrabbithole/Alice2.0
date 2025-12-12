#!/usr/bin/env python3
"""
StockingCog with buildable assemblies (snowman example)

- Persists per-user stickers/buildables to data/stockings.json
- Loads sticker/buildable defs from data/stickers.json and data/buildables.json
- API: award_sticker(user_id, sticker_key, channel) and award_part(user_id, buildable_key, part_key, channel, announce=True)
- Renders composites into data/stocking_assets/*
- Minimal commands omitted here; keep your existing command handlers if you have them
"""
from __future__ import annotations

import asyncio
import json
import logging
from io import BytesIO
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

import discord
from discord import app_commands
from discord.ext import commands

# Optional helper; your repo may provide this if you use the gallery renderer
try:
    from ui.stocking_render_helpers import render_stocking_image_auto
except Exception:
    render_stocking_image_auto = None  # guarded use

logger = logging.getLogger(__name__)

# Data paths
DATA_DIR = Path("data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
ASSETS_DIR = DATA_DIR / "stocking_assets"
ASSETS_DIR.mkdir(parents=True, exist_ok=True)

COLLECTED_PATH = DATA_DIR / "collected_pieces.json"
STOCKINGS_FILE = DATA_DIR / "stockings.json"
STICKERS_DEF_FILE = DATA_DIR / "stickers.json"
BUILDABLES_DEF_FILE = DATA_DIR / "buildables.json"

DEFAULT_CAPACITY = 12
AUTO_ROLE_ID: Optional[int] = 1448857904282206208

_lock = asyncio.Lock()

# Default color fallback
DEFAULT_PART_COLOR = discord.Color.from_rgb(47, 49, 54).value  # use int for generated maps

# Canonical small mapping for common part names; used as first preference.
_CANONICAL_EMOJI = {
    "hat": "🎩",
    "scarf": "🧣",
    "carrot": "🥕",
    "eyes": "👀",
    "mouth": "😄",
    "buttons": "⚪",
    "arms": "✋",
}
_CANONICAL_COLORS = {
    "hat": 0x001F3B,       # navy
    "scarf": 0x8B0000,     # dark red
    "carrot": 0xFFA500,    # orange
    "eyes": 0x9E9E9E,      # gray
    "mouth": 0x9E9E9E,
    "buttons": 0x9E9E9E,
    "arms": 0x6B4423,      # brown-ish
}


def _generate_part_maps_from_buildables() -> Tuple[Dict[str, str], Dict[str, int]]:
    """Return (part_emoji, part_colors) keyed by lowercased part_key."""
    parts_keys = set()
    try:
        if BUILDABLES_DEF_FILE.exists():
            data = json.loads(BUILDABLES_DEF_FILE.read_text(encoding="utf-8") or "{}")
            for bdef in (data or {}).values():
                for pk in (bdef.get("parts") or {}).keys():
                    parts_keys.add(pk)
    except Exception:
        parts_keys = set()

    part_emoji: Dict[str, str] = {}
    part_colors: Dict[str, int] = {}
    for pk in sorted(parts_keys):
        key_lower = pk.lower()
        emoji = _CANONICAL_EMOJI.get(key_lower, "🔸")
        color = _CANONICAL_COLORS.get(key_lower, DEFAULT_PART_COLOR)
        part_emoji[key_lower] = emoji
        part_colors[key_lower] = color

    if not part_emoji:
        # fallback canonical set
        for k, v in _CANONICAL_EMOJI.items():
            part_emoji[k] = v
            part_colors[k] = _CANONICAL_COLORS.get(k, DEFAULT_PART_COLOR)

    return part_emoji, part_colors


# generate maps
PART_EMOJI, PART_COLORS = _generate_part_maps_from_buildables()


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


def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)


def persist_awarded_part(user_id: int, buildable_key: str, part_key: str) -> bool:
    """
    Add `part_key` for user_id/buildable_key into data/collected_pieces.json if not present.
    Returns True if file was modified (part added), False if already present.
    Logs helpful info for debugging.
    """
    try:
        data = {}
        if COLLECTED_PATH.exists():
            try:
                text = COLLECTED_PATH.read_text(encoding="utf-8").strip()
                data = json.loads(text) if text else {}
            except Exception as e:
                logger.warning("Could not parse %s: %s — starting with empty data", COLLECTED_PATH, e)
                data = {}

        user_key = str(user_id)
        user_entries = data.setdefault(user_key, {})
        parts = user_entries.setdefault(buildable_key, [])

        part_key_str = str(part_key)
        if part_key_str in parts:
            logger.info("persist_awarded_part: user=%s already has part=%s for %s", user_id, part_key_str, buildable_key)
            return False

        parts.append(part_key_str)
        COLLECTED_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("persist_awarded_part: persisted user=%s part=%s for %s", user_id, part_key_str, buildable_key)
        return True
    except Exception as exc:
        logger.exception("persist_awarded_part: failed to persist award for user=%s (%s/%s): %s", user_id, buildable_key, part_key, exc)
        return False


class StockingCog(commands.Cog, name="StockingCog"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        ensure_dirs()
        self._data: Dict[str, Dict[str, Any]] = {}
        self._stickers_def: Dict[str, Dict[str, Any]] = {}
        self._buildables_def: Dict[str, Dict[str, Any]] = {}
        self._load_all()
        logger.info("StockingCog initialized")

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
                # sensible default if missing
                self._buildables_def = {
                    "snowman": {
                        "base": "buildables/snowman/base.png",
                        "parts": {
                            "carrot": {"file": "buildables/snowman/parts/carrot.png", "offset": [350, 170], "z": 10},
                            "hat": {"file": "buildables/snowman/parts/hat.png", "offset": [210, 60], "z": 30},
                            "scarf": {"file": "buildables/snowman/parts/scarf.png", "offset": [170, 240], "z": 20},
                            "eyes": {"file": "buildables/snowman/parts/eyes.png", "offset": [320, 80], "z": 35},
                            "mouth": {"file": "buildables/snowman/parts/mouth.png", "offset": [330,120], "z": 30},
                            "buttons": {"file": "buildables/snowman/parts/buttons.png", "offset": [340,200], "z": 20},
                            "arms": {"file": "buildables/snowman/parts/arms.png", "offset": [120,160], "z": 10}
                        },
                        "capacity_slots": 7,
                        "role_on_complete": None
                    }
                }
                try:
                    with BUILDABLES_DEF_FILE.open("w", encoding="utf-8") as fh:
                        json.dump(self._buildables_def, fh, ensure_ascii=False, indent=2)
                except Exception:
                    logger.exception("Failed to write default buildables file")
        except Exception:
            self._buildables_def = {}

    async def _save(self) -> None:
        async with _lock:
            try:
                STOCKINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
                with STOCKINGS_FILE.open("w", encoding="utf-8") as fh:
                    json.dump(self._data, fh, ensure_ascii=False, indent=2)
            except Exception:
                logger.exception("Failed to save stockings data")

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
                logger.exception("award_sticker: failed to announce")
        try:
            await self._maybe_award_role(user_id, channel.guild if channel is not None else None)
        except Exception:
            logger.exception("award_sticker: failed to maybe_award_role")
        return True

    # buildables (parts)
    async def award_part(self, user_id: int, buildable_key: str, part_key: str, channel: Optional[discord.TextChannel] = None, *, announce: bool = True) -> bool:
        build_def = self._buildables_def.get(buildable_key)
        if not build_def:
            logger.debug("award_part: no build_def for %s", buildable_key)
            return False
        parts_def = build_def.get("parts", {})
        if part_key not in parts_def:
            logger.debug("award_part: part_key %s not in definitions for %s", part_key, buildable_key)
            return False

        user = self._ensure_user(user_id)
        b = user.setdefault("buildables", {}).setdefault(buildable_key, {"parts": [], "completed": False})

        if part_key in b["parts"]:
            if announce and channel:
                try:
                    member = channel.guild.get_member(user_id) if channel and channel.guild else None
                    mention = member.mention if member else f"<@{user_id}>"
                    await channel.send(f"{mention} already has the **{part_key}** for {buildable_key}.")
                except Exception:
                    logger.exception("award_part: failed to announce already-has")
            return False

        b["parts"].append(part_key)
        await self._save()

        out_path = None
        try:
            out_path = await self.render_buildable(user_id, buildable_key)
        except Exception:
            out_path = None

        if announce and channel:
            mention = f"<@{user_id}>"
            try:
                member = channel.guild.get_member(user_id) if channel and channel.guild else None
                if member:
                    mention = member.mention
            except Exception:
                logger.exception("award_part: failed to get member for mention")

            # color lookup uses lowercased keys
            color_int = PART_COLORS.get(part_key.lower(), DEFAULT_PART_COLOR)
            color = discord.Color(color_int)
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
                    logger.exception("award_part: failed to send embed or fallback text")

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
                                logger.exception("award_part: failed to announce completion fallback")
                except Exception:
                    logger.exception("award_part: failed to assign completion role")

        return True

    # render buildable composite
    async def render_buildable(self, user_id: int, buildable_key: str) -> Optional[Path]:
        try:
            from PIL import Image as PILImage
        except Exception:
            logger.debug("render_buildable: PIL not available")
            return None

        build_def = self._buildables_def.get(buildable_key)
        if not build_def:
            logger.debug("render_buildable: no build_def for %s", buildable_key)
            return None
        base_rel = build_def.get("base")
        if not base_rel:
            logger.debug("render_buildable: no base for %s", buildable_key)
            return None

        # Accept absolute or relative; prefer ASSETS_DIR relative
        base_path = Path(base_rel)
        if not base_path.exists():
            base_path = ASSETS_DIR / base_rel
        if not base_path.exists():
            logger.debug("render_buildable: base image not found for %s at %s", buildable_key, base_path)
            return None

        try:
            base = PILImage.open(base_path).convert("RGBA")
        except Exception:
            logger.exception("render_buildable: failed to open base image %s", base_path)
            return None

        user = self._ensure_user(user_id)
        ub = user.get("buildables", {}).get(buildable_key, {"parts": []})
        user_parts = ub.get("parts", [])

        part_items: List[Tuple[int, "PILImage.Image", Tuple[int, int]]] = []
        for pkey in user_parts:
            pdef = build_def.get("parts", {}).get(pkey)
            if not pdef:
                logger.debug("render_buildable: no part def for %s (user part %s)", buildable_key, pkey)
                continue
            ppath = Path(pdef.get("file", ""))
            if not ppath.exists():
                ppath = ASSETS_DIR / pdef.get("file", "")
            if not ppath.exists():
                logger.debug("render_buildable: asset file missing %s", ppath)
                continue
            try:
                img = PILImage.open(ppath).convert("RGBA")
                offset = tuple(pdef.get("offset", [0, 0]))
                z = int(pdef.get("z", 0))
                part_items.append((z, img, offset))
            except Exception:
                logger.exception("render_buildable: failed to open/convert part image %s", ppath)
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
                    logger.exception("render_buildable: failed to paste image at %s,%s", ox, oy)
                    continue

        out = ASSETS_DIR / f"{buildable_key}_user_{user_id}.png"
        try:
            base.save(out, format="PNG")
            return out
        except Exception:
            logger.exception("render_buildable: failed to save composite %s", out)
            return None

    async def _render_user_stocking(self, user_id: int) -> Optional[Path]:
        loop = asyncio.get_running_loop()
        user_stickers = self._ensure_user(user_id).get("stickers", [])
        stickers_def = self._stickers_def
        if render_stocking_image_auto is None:
            logger.debug("_render_user_stocking: render_stocking_image_auto not available")
            return None
        try:
            out = await loop.run_in_executor(
                None,
                render_stocking_image_auto,
                user_id,
                user_stickers,
                stickers_def,
                ASSETS_DIR,
                "template.png",
                4,
                3,
                f"stocking_{user_id}.png"
            )
            return out
        except Exception:
            logger.exception("_render_user_stocking: failed to render stocking image")
            return None

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
                        logger.exception("_maybe_award_role: failed to notify")
            except Exception:
                logger.exception("_maybe_award_role: failed to award role")

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