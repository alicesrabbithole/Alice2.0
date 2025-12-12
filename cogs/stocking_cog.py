#!/usr/bin/env python3
"""
StockingCog with buildable assemblies (snowman example)

- Persists per-user stickers/buildables to data/stockings.json
- Loads sticker/buildable defs from data/stickers.json and data/buildables.json
- API: award_sticker(user_id, sticker_key, channel) and award_part(user_id, buildable_key, part_key, channel, announce=True)
- Provides remove_part / revoke_part to allow removal (admin use)
- Renders composites into data/stocking_assets/*
- Adds /mysnowman command to show the user's assembled snowman (composite of collected parts), falling back to base image.
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

# Shared theme/constants (emoji/color and generator)
from utils.snowman_theme import DEFAULT_COLOR, generate_part_maps_from_buildables

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

STOCKINGS_FILE = DATA_DIR / "stockings.json"
STICKERS_DEF_FILE = DATA_DIR / "stickers.json"
BUILDABLES_DEF_FILE = DATA_DIR / "buildables.json"

DEFAULT_CAPACITY = 12
AUTO_ROLE_ID: Optional[int] = 1448857904282206208

_lock = asyncio.Lock()

# PART_EMOJI / PART_COLORS derived from buildables.json (falls back to canonical maps in theme)
PART_EMOJI, PART_COLORS = generate_part_maps_from_buildables()


# --- helper functions for autocomplete ---
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

            # color lookup uses lowercased keys, fallback to DEFAULT_COLOR from theme
            color_int = PART_COLORS.get(part_key.lower(), DEFAULT_COLOR)
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

    # removal API — allow admin or other cogs to remove a part
    async def remove_part(self, user_id: int, buildable_key: str, part_key: str) -> bool:
        """
        Remove a previously-awarded part from a user's buildable.
        Returns True if removed and saved, False if the user didn't have that part.
        """
        user = self._ensure_user(user_id)
        b = user.get("buildables", {}).get(buildable_key)
        if not b:
            return False
        parts = b.get("parts", [])
        if part_key not in parts:
            return False
        try:
            parts.remove(part_key)
            # If the buildable was marked completed, unset it if not complete anymore
            build_def = self._buildables_def.get(buildable_key, {})
            parts_def = build_def.get("parts", {}) or {}
            capacity_slots = int(build_def.get("capacity_slots", len(parts_def)))
            if len(parts) < min(capacity_slots, len(parts_def)):
                b["completed"] = False
            await self._save()
            return True
        except Exception:
            logger.exception("remove_part: failed to remove part %s for user %s", part_key, user_id)
            return False

    # alias for different naming conventions
    async def revoke_part(self, user_id: int, buildable_key: str, part_key: str) -> bool:
        return await self.remove_part(user_id, buildable_key, part_key)

    # render buildable composite
    async def render_buildable(self, user_id: int, buildable_key: str) -> Optional[Path]:
        """
        Render a composite for a user's buildable.
        """
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

        # Resolve base path (accept absolute or relative to assets dir)
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

        # Load user parts in order and build list of (z, image, paste_xy) where paste_xy is (x,y)
        user = self._ensure_user(user_id)
        ub = user.get("buildables", {}).get(buildable_key, {"parts": []})
        user_parts = ub.get("parts", [])

        part_items = []  # List[Tuple[int, PILImage.Image, Tuple[int,int]]]
        for pkey in user_parts:
            pdef = build_def.get("parts", {}).get(pkey)
            if not pdef:
                logger.debug("render_buildable: no part def for %s (user part %s)", buildable_key, pkey)
                continue

            # Resolve part image path (accept absolute or relative)
            ppath = Path(pdef.get("file", "")) if pdef.get("file") else None
            if not ppath or not ppath.exists():
                # try assets dir relative
                ppath = ASSETS_DIR / pdef.get("file", "")
            if not ppath.exists():
                logger.debug("render_buildable: asset file missing %s for part %s", pdef.get("file", ""), pkey)
                continue

            try:
                img = PILImage.open(ppath).convert("RGBA")
            except Exception:
                logger.exception("render_buildable: failed to open/convert part image %s", ppath)
                continue

            # Determine paste mode: full-canvas vs sprite with offset
            full_canvas_flag = bool(pdef.get("full_canvas")) if isinstance(pdef.get("full_canvas"), (bool, int)) else False
            if not full_canvas_flag:
                try:
                    if img.size == base.size:
                        logger.info("render_buildable: auto-detected full_canvas for part %s (img size == base size)", pkey)
                        full_canvas_flag = True
                except Exception:
                    pass

            if full_canvas_flag:
                paste_x, paste_y = 0, 0
                logger.debug("render_buildable: part %s is full_canvas -> paste at (0,0)", pkey)
            else:
                offset = pdef.get("offset", [0, 0]) or [0, 0]
                try:
                    paste_x = int(offset[0])
                    paste_y = int(offset[1])
                except Exception:
                    paste_x, paste_y = 0, 0
                logger.debug("render_buildable: part %s using offset %s -> paste at (%s,%s)", pkey, offset, paste_x, paste_y)

            try:
                z = int(pdef.get("z", 0))
            except Exception:
                z = 0

            part_items.append((z, img, (paste_x, paste_y)))

        # Sort by z and paste
        part_items.sort(key=lambda t: t[0])
        for (_z, img, (ox, oy)) in part_items:
            try:
                base.paste(img, (int(ox), int(oy)), img)
            except Exception:
                # Try to clamp into base bounds if offset would result in out-of-range
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

    # /mysnowman command — show the user's assembled snowman (composite) if parts exist, otherwise show base
    @commands.hybrid_command(name="mysnowman", description="Show your snowman assembled from collected parts (falls back to base).")
    async def mysnowman(self, ctx: commands.Context):
        user_id = getattr(ctx.author, "id", None)
        if user_id is None:
            await self._ephemeral_reply(ctx, "Could not determine user id.")
            return

        build_def = self._buildables_def.get("snowman")
        if not build_def:
            await self._ephemeral_reply(ctx, "No snowman buildable configured.")
            return

        # First, try to render a composite for this user
        composite_path = None
        try:
            composite_path = await self.render_buildable(user_id, "snowman")
        except Exception:
            composite_path = None

        if composite_path and composite_path.exists():
            # Send composite image
            try:
                file = discord.File(composite_path, filename=composite_path.name)
                embed = discord.Embed(title="Your Snowman", color=discord.Color.dark_blue())
                embed.set_image(url=f"attachment://{composite_path.name}")
                await ctx.reply(embed=embed, file=file, mention_author=False)
                return
            except Exception:
                logger.exception("mysnowman: failed to send composite image, falling back to base")

        # Fallback: send the base only
        base_rel = build_def.get("base")
        if not base_rel:
            await self._ephemeral_reply(ctx, "Snowman base image not configured.")
            return

        # Resolve path: accept absolute or relative-to-assets
        base_path = Path(base_rel)
        if not base_path.exists():
            base_path = ASSETS_DIR / base_rel
        if not base_path.exists():
            # Also try repo-root relative
            base_path = Path.cwd() / base_rel
        if not base_path.exists():
            await self._ephemeral_reply(ctx, "Base snowman image not found on disk.")
            return

        try:
            from PIL import Image as PILImage  # local import to avoid hard dependency until used
            with PILImage.open(base_path).convert("RGBA") as img:
                buf = BytesIO()
                img.save(buf, format="PNG")
                buf.seek(0)
                df = discord.File(buf, filename="mysnowman.png")
                embed = discord.Embed(title="Your Snowman (base)", color=discord.Color.dark_blue())
                embed.set_image(url="attachment://mysnowman.png")
                await ctx.reply(embed=embed, file=df, mention_author=False)
                return
        except Exception:
            # Fallback: send the file directly if PIL unavailable or fails
            try:
                await ctx.reply(file=discord.File(base_path, filename=base_path.name), mention_author=False)
                return
            except Exception as exc:
                logger.exception("mysnowman: failed to send base image: %s", exc)
                await self._ephemeral_reply(ctx, "Failed to send the base snowman image.")
                return

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

    async def _ephemeral_reply(self, ctx: commands.Context, content: str, *, mention_author: bool = False) -> None:
        try:
            if getattr(ctx, "interaction", None) is not None and getattr(ctx.interaction, "response", None) is not None:
                if not ctx.interaction.response.is_done():
                    await ctx.interaction.response.send_message(content, ephemeral=True)
                    return
        except Exception:
            pass
        try:
            await ctx.reply(content, mention_author=mention_author)
        except Exception:
            try:
                await ctx.send(content)
            except Exception:
                pass


async def setup(bot: commands.Bot):
    await bot.add_cog(StockingCog(bot))