#!/usr/bin/env python3
"""
Refactored StockingCog (full replacement)

Goals:
- Reliable persistence (async-safe).
- Clear logging for award/send/save operations.
- Robust rendering with Pillow when available; graceful fallbacks.
- /mysnowman hybrid command that will attach the composite or a part/base image.
- Award APIs compatible with existing admin/rumble cogs.
- Defensive checks for guild/channel/member contexts and permissions.

Install notes:
- Pillow (PIL) is optional but recommended for rendering composites:
  .venv/bin/pip install Pillow
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

# optional theme helpers in repo (falls back to safe defaults)
try:
    from utils.snowman_theme import DEFAULT_COLOR, generate_part_maps_from_buildables
except Exception:
    DEFAULT_COLOR = 0x2F3136

    def generate_part_maps_from_buildables():
        return ({}, {})

# optional gallery renderer (not required)
try:
    from ui.stocking_render_helpers import render_stocking_image_auto
except Exception:
    render_stocking_image_auto = None

logger = logging.getLogger(__name__)

# Data paths
ROOT = Path.cwd()
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
ASSETS_DIR = DATA_DIR / "stocking_assets"
ASSETS_DIR.mkdir(parents=True, exist_ok=True)

STOCKINGS_FILE = DATA_DIR / "stockings.json"
STICKERS_DEF_FILE = DATA_DIR / "stickers.json"
BUILDABLES_DEF_FILE = DATA_DIR / "buildables.json"

DEFAULT_CAPACITY = 12
AUTO_ROLE_ID: Optional[int] = 1448857904282206208

_save_lock = asyncio.Lock()

# Derived maps for emoji/colors
PART_EMOJI, PART_COLORS = generate_part_maps_from_buildables()


async def _autocomplete_sticker(interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
    try:
        if STICKERS_DEF_FILE.exists():
            data = json.load(STICKERS_DEF_FILE.open("r", encoding="utf-8"))
        else:
            data = {}
    except Exception:
        data = {}
    cur = (current or "").strip().lower()
    choices: List[app_commands.Choice[str]] = []
    for k in sorted(data.keys()):
        if not cur or k.lower().startswith(cur):
            choices.append(app_commands.Choice(name=k, value=k))
            if len(choices) >= 25:
                break
    return choices


def _safe_json_load(path: Path) -> Any:
    try:
        if path.exists():
            return json.load(path.open("r", encoding="utf-8"))
    except Exception:
        logger.exception("Failed to load JSON from %s", path)
    return None


class StockingCog(commands.Cog, name="StockingCog"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._data: Dict[str, Dict[str, Any]] = {}
        self._stickers_def: Dict[str, Dict[str, Any]] = {}
        self._buildables_def: Dict[str, Dict[str, Any]] = {}
        self._load_all()
        logger.info("StockingCog initialized (data keys=%s)", list(self._data.keys())[:5])

    def _load_all(self) -> None:
        # stockings
        try:
            if STOCKINGS_FILE.exists():
                with STOCKINGS_FILE.open("r", encoding="utf-8") as fh:
                    self._data = json.load(fh) or {}
            else:
                self._data = {}
        except Exception:
            logger.exception("Failed to load stockings data, starting empty")
            self._data = {}

        # stickers
        try:
            if STICKERS_DEF_FILE.exists():
                with STICKERS_DEF_FILE.open("r", encoding="utf-8") as fh:
                    self._stickers_def = json.load(fh) or {}
            else:
                self._stickers_def = {}
        except Exception:
            logger.exception("Failed to load stickers definitions")
            self._stickers_def = {}

        # buildables
        try:
            if BUILDABLES_DEF_FILE.exists():
                with BUILDABLES_DEF_FILE.open("r", encoding="utf-8") as fh:
                    self._buildables_def = json.load(fh) or {}
            else:
                # create a minimal snowman default if none present
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
            logger.exception("Failed to load buildables def")
            self._buildables_def = {}

    async def _save(self) -> None:
        async with _save_lock:
            try:
                STOCKINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
                with STOCKINGS_FILE.open("w", encoding="utf-8") as fh:
                    json.dump(self._data, fh, ensure_ascii=False, indent=2)
                logger.debug("_save: wrote %s", STOCKINGS_FILE)
            except Exception:
                logger.exception("Failed to save stockings data")

    def _ensure_user(self, user_id: int) -> Dict[str, Any]:
        key = str(user_id)
        if key not in self._data:
            self._data[key] = {"stickers": [], "capacity": DEFAULT_CAPACITY, "buildables": {}}
        return self._data[key]

    def get_user_stocking(self, user_id: int) -> Dict[str, Any]:
        return self._ensure_user(user_id)

    # -------------------------
    # Awarding APIs
    # -------------------------
    async def award_sticker(self, user_id: int, sticker_key: str, channel: Optional[discord.TextChannel] = None, *, announce: bool = True) -> bool:
        if sticker_key not in self._stickers_def:
            logger.debug("award_sticker: unknown sticker %s", sticker_key)
            return False
        user = self._ensure_user(user_id)
        user.setdefault("stickers", []).append(sticker_key)
        await self._save()
        if announce and channel:
            try:
                member = channel.guild.get_member(user_id) if channel and channel.guild else None
                mention = member.mention if member else f"<@{user_id}>"
                await channel.send(f"🎉 {mention} earned a **{sticker_key}** sticker! Use `/mysnowman` to view your snowman.")
                logger.info("award_sticker: announced %s in channel %s", sticker_key, getattr(channel, "id", None))
            except Exception:
                logger.exception("award_sticker: failed to announce sticker award")
        # try auto role (best-effort)
        try:
            await self._maybe_award_role(user_id, channel.guild if channel is not None else None)
        except Exception:
            logger.exception("award_sticker: maybe_award_role failed")
        return True

    async def award_part(self, user_id: int, buildable_key: str, part_key: str, channel: Optional[discord.TextChannel] = None, *, announce: bool = True) -> bool:
        """
        Main award API used by rumble listener and admin commands.
        Returns True on persisted award; False if skipped (unknown buildable/part or user already has it).
        """
        build_def = self._buildables_def.get(buildable_key)
        if not build_def:
            logger.warning("award_part: unknown buildable %s", buildable_key)
            return False
        parts_def = build_def.get("parts", {}) or {}
        if part_key not in parts_def:
            logger.warning("award_part: unknown part %s for %s", part_key, buildable_key)
            return False

        user = self._ensure_user(user_id)
        b = user.setdefault("buildables", {}).setdefault(buildable_key, {"parts": [], "completed": False})

        if part_key in b.get("parts", []):
            logger.info("award_part: user %s already has %s for %s", user_id, part_key, buildable_key)
            # announce if requested
            if announce and channel:
                try:
                    member = channel.guild.get_member(user_id) if channel and channel.guild else None
                    mention = member.mention if member else f"<@{user_id}>"
                    await channel.send(f"{mention} already has the **{part_key}** for {buildable_key}.")
                except Exception:
                    logger.exception("award_part: failed to announce already-has")
            return False

        # persist
        b["parts"].append(part_key)
        await self._save()

        # attempt to render composite (best-effort)
        composite_path: Optional[Path] = None
        try:
            composite_path = await self.render_buildable(user_id, buildable_key)
        except Exception:
            composite_path = None

        # Announce award to channel (if provided). Provide logging for success/failure.
        if announce and channel:
            mention = f"<@{user_id}>"
            try:
                member = channel.guild.get_member(user_id) if channel and channel.guild else None
                if member:
                    mention = member.mention
            except Exception:
                logger.debug("award_part: could not resolve member mention for %s", user_id)

            color_int = PART_COLORS.get(part_key.lower(), DEFAULT_COLOR)
            color = discord.Color(color_int)
            emb = discord.Embed(title=f"Part Awarded — {buildable_key}", description=f"🎉 {mention} received the **{part_key}** for **{buildable_key}**!", color=color)

            try:
                if composite_path and composite_path.exists():
                    f = discord.File(composite_path, filename=composite_path.name)
                    emb.set_image(url=f"attachment://%s" % composite_path.name)
                    await channel.send(embed=emb, file=f)
                    logger.info("award_part: sent composite announcement for %s to channel %s", part_key, getattr(channel, "id", None))
                else:
                    # try to attach the part image as a thumbnail (fallback)
                    part_file = Path(parts_def.get(part_key, {}).get("file", "")) if parts_def.get(part_key, {}).get("file") else None
                    if not part_file or not part_file.exists():
                        part_file = ASSETS_DIR / parts_def.get(part_key, {}).get("file", "")
                    if not part_file or not part_file.exists():
                        part_file = ASSETS_DIR / f"stickers/{part_key}.png"
                    if part_file and part_file.exists():
                        f = discord.File(part_file, filename=part_file.name)
                        emb.set_thumbnail(url=f"attachment://%s" % part_file.name)
                        await channel.send(embed=emb, file=f)
                        logger.info("award_part: sent part-thumbnail announcement for %s to channel %s", part_key, getattr(channel, "id", None))
                    else:
                        await channel.send(embed=emb)
                        logger.info("award_part: sent text/embed announcement for %s to channel %s", part_key, getattr(channel, "id", None))
            except Exception:
                logger.exception("award_part: failed to announce award for %s to channel %s", part_key, getattr(channel, "id", None))

        # handle completion & role grant
        try:
            capacity_slots = int(build_def.get("capacity_slots", len(parts_def)))
        except Exception:
            capacity_slots = len(parts_def)
        total = len(b.get("parts", []))

        if total >= capacity_slots or total >= len(parts_def):
            b["completed"] = True
            await self._save()
            role_id = build_def.get("role_on_complete") or AUTO_ROLE_ID
            # try to determine guild context
            guild = channel.guild if channel and getattr(channel, "guild", None) else None
            if not guild:
                # best-effort: find any guild that has the member cached
                try:
                    for g in self.bot.guilds:
                        if g.get_member(user_id):
                            guild = g
                            break
                except Exception:
                    guild = None

            if role_id and guild:
                try:
                    role = guild.get_role(int(role_id))
                    member = guild.get_member(user_id) or await guild.fetch_member(user_id)
                    if role and member and role not in member.roles:
                        bot_member = guild.me
                        if not bot_member or not bot_member.guild_permissions.manage_roles:
                            logger.warning("award_part: cannot grant role %s in guild %s (missing perms)", role_id, guild.id)
                        elif role.position >= (bot_member.top_role.position if bot_member.top_role else -1):
                            logger.warning("award_part: cannot grant role %s in guild %s (hierarchy)", role_id, guild.id)
                        else:
                            await member.add_roles(role, reason=f"{buildable_key} completed")
                            logger.info("award_part: granted completion role %s to %s in guild %s", role_id, user_id, guild.id)
                            # persist that we granted the role
                            try:
                                rec = self._ensure_user(user_id)
                                brec = rec.setdefault("buildables", {}).setdefault(buildable_key, {})
                                brec["role_granted"] = True
                                await self._save()
                            except Exception:
                                logger.exception("award_part: failed to persist role_granted flag")
                            # announce completion if channel present
                            if channel and getattr(channel, "guild", None):
                                try:
                                    await channel.send(embed=discord.Embed(title=f"{buildable_key} Completed!", description=f"🎉 {member.mention} completed **{buildable_key}** and was awarded {role.mention}!", color=discord.Color.green()))
                                except Exception:
                                    logger.exception("award_part: failed to announce completion")
                except Exception:
                    logger.exception("award_part: role grant flow failed")

        return True

    # -------------------------
    # Removal APIs
    # -------------------------
    async def remove_part(self, user_id: int, buildable_key: str, part_key: str) -> bool:
        user = self._ensure_user(user_id)
        b = user.get("buildables", {}).get(buildable_key)
        if not b:
            return False
        parts = b.get("parts", [])
        if part_key not in parts:
            return False
        try:
            parts.remove(part_key)
            # recalc completed flag
            build_def = self._buildables_def.get(buildable_key, {}) or {}
            parts_def = build_def.get("parts", {}) or {}
            capacity_slots = int(build_def.get("capacity_slots", len(parts_def)))
            if len(parts) < min(capacity_slots, len(parts_def)):
                b["completed"] = False
            await self._save()
            return True
        except Exception:
            logger.exception("remove_part: failed removing %s from %s", part_key, user_id)
            return False

    async def revoke_part(self, user_id: int, buildable_key: str, part_key: str) -> bool:
        return await self.remove_part(user_id, buildable_key, part_key)

    # -------------------------
    # Rendering
    # -------------------------
    async def render_buildable(self, user_id: int, buildable_key: str) -> Optional[Path]:
        """
        Render composite PNG for a user's buildable. Returns path to saved PNG or None.
        Uses Pillow if available; otherwise may use a repo-provided renderer or return None.
        """
        # prefer dedicated helper if available
        if render_stocking_image_auto:
            try:
                out = await render_stocking_image_auto(self._data, user_id, buildable_key, ASSETS_DIR)
                return Path(out) if out else None
            except Exception:
                logger.exception("render_buildable: plugin renderer failed")

        try:
            from PIL import Image as PILImage
        except Exception:
            logger.debug("render_buildable: Pillow not available")
            return None

        build_def = self._buildables_def.get(buildable_key)
        if not build_def:
            logger.debug("render_buildable: no build_def for %s", buildable_key)
            return None

        base_rel = build_def.get("base")
        if not base_rel:
            logger.debug("render_buildable: no base for %s", buildable_key)
            return None

        # resolve base path
        base_path = Path(base_rel)
        if not base_path.exists():
            base_path = ASSETS_DIR / base_rel
        if not base_path.exists():
            base_path = ROOT / base_rel
        if not base_path.exists():
            logger.debug("render_buildable: base not found %s", base_rel)
            return None

        try:
            base_img = PILImage.open(base_path).convert("RGBA")
        except Exception:
            logger.exception("render_buildable: failed to open base image %s", base_path)
            return None

        user = self._ensure_user(user_id)
        ub = user.get("buildables", {}).get(buildable_key, {"parts": []})
        user_parts = ub.get("parts", [])

        overlay_items: List[Tuple[int, "PIL.Image.Image", Tuple[int, int]]] = []
        for pkey in user_parts:
            pdef = build_def.get("parts", {}).get(pkey)
            if not pdef:
                logger.debug("render_buildable: missing part def for %s", pkey)
                continue
            # find part file
            ppath = Path(pdef.get("file", "")) if pdef.get("file") else None
            if not ppath or not ppath.exists():
                ppath = ASSETS_DIR / pdef.get("file", "")
            if not ppath or not ppath.exists():
                ppath = ROOT / pdef.get("file", "")
            if not ppath or not ppath.exists():
                logger.debug("render_buildable: part file not found for %s -> %s", pkey, pdef.get("file"))
                continue
            try:
                img = PILImage.open(ppath).convert("RGBA")
            except Exception:
                logger.exception("render_buildable: failed to open part image %s", ppath)
                continue

            full_canvas = bool(pdef.get("full_canvas")) if isinstance(pdef.get("full_canvas"), (bool, int)) else False
            if not full_canvas:
                try:
                    if img.size == base_img.size:
                        full_canvas = True
                except Exception:
                    pass

            if full_canvas:
                ox, oy = 0, 0
            else:
                off = pdef.get("offset", [0, 0]) or [0, 0]
                try:
                    ox, oy = int(off[0]), int(off[1])
                except Exception:
                    ox, oy = 0, 0
            try:
                z = int(pdef.get("z", 0))
            except Exception:
                z = 0

            overlay_items.append((z, img, (ox, oy)))

        overlay_items.sort(key=lambda t: t[0])
        for (_z, img, (ox, oy)) in overlay_items:
            try:
                base_img.paste(img, (int(ox), int(oy)), img)
            except Exception:
                try:
                    w, h = base_img.size
                    px = max(0, min(w - 1, int(ox)))
                    py = max(0, min(h - 1, int(oy)))
                    base_img.paste(img, (px, py), img)
                except Exception:
                    logger.exception("render_buildable: paste failed for item at %s,%s", ox, oy)

        out_path = ASSETS_DIR / f"{buildable_key}_user_{user_id}.png"
        try:
            base_img.save(out_path, format="PNG")
            logger.debug("render_buildable: saved composite %s", out_path)
            return out_path
        except Exception:
            logger.exception("render_buildable: failed to save composite to %s", out_path)
            return None

    # -------------------------
    # /mysnowman command
    # -------------------------
    @commands.hybrid_command(name="mysnowman", description="Show your snowman assembled from collected parts.")
    async def mysnowman(self, ctx: commands.Context):
        user = getattr(ctx.author, "id", None)
        if not user:
            await self._ephemeral_reply(ctx, "Could not determine your user id.")
            return

        build_key = "snowman"
        build_def = self._buildables_def.get(build_key)
        if not build_def:
            await self._ephemeral_reply(ctx, "No snowman buildable configured.")
            return

        # ensure record
        rec = self._ensure_user(user)
        b = rec.get("buildables", {}).get(build_key, {"parts": [], "completed": False})
        user_parts = b.get("parts", [])
        parts_def = build_def.get("parts", {}) or {}
        capacity_slots = int(build_def.get("capacity_slots", len(parts_def)))

        # ensure completion role if eligible
        try:
            is_complete = bool(b.get("completed")) or (len(user_parts) >= capacity_slots or len(user_parts) >= len(parts_def))
            if is_complete:
                await self._grant_buildable_completion_role(user, build_key, ctx.guild, ctx.channel)
        except Exception:
            logger.exception("mysnowman: error ensuring completion role")

        # try composite
        composite = None
        try:
            composite = await self.render_buildable(user, build_key)
        except Exception:
            composite = None

        embed = discord.Embed(title="Your Snowman", color=discord.Color.dark_blue())
        embed.add_field(name="Parts collected", value=f"{len(user_parts)}/{capacity_slots}", inline=False)
        embed.add_field(name="Parts", value=", ".join(user_parts) if user_parts else "(none)", inline=False)

        if composite and composite.exists():
            try:
                f = discord.File(composite, filename=composite.name)
                embed.set_image(url=f"attachment://%s" % composite.name)
                await ctx.reply(embed=embed, file=f, mention_author=False)
                return
            except Exception:
                logger.exception("mysnowman: failed to send composite image")

        # fallback to base or part thumbnails
        base_rel = build_def.get("base")
        candidate = None
        if base_rel:
            base_path = Path(base_rel)
            if not base_path.exists():
                base_path = ASSETS_DIR / base_rel
            if not base_path.exists():
                base_path = ROOT / base_rel
            if base_path.exists():
                candidate = base_path

        # if no base or we still want to highlight last part, try last part thumbnail
        if not candidate and user_parts:
            last = user_parts[-1]
            pdef = parts_def.get(last, {}) or {}
            ppath = Path(pdef.get("file", "")) if pdef.get("file") else None
            if not ppath or not ppath.exists():
                ppath = ASSETS_DIR / pdef.get("file", "")
            if not ppath or not ppath.exists():
                ppath = ASSETS_DIR / f"stickers/{last}.png"
            if ppath.exists():
                candidate = ppath

        if candidate:
            try:
                # if candidate equals a path to file, attach it
                f = discord.File(candidate, filename=candidate.name)
                embed.set_image(url=f"attachment://%s" % candidate.name)
                await ctx.reply(embed=embed, file=f, mention_author=False)
                return
            except Exception:
                logger.exception("mysnowman: failed to send fallback image %s", candidate)

        # final fallback: simple text reply
        await self._ephemeral_reply(ctx, f"You have {len(user_parts)} parts: {', '.join(user_parts) if user_parts else '(none)'}.")

    # -------------------------
    # Role helpers & events
    # -------------------------
    async def _maybe_award_role(self, user_id: int, guild: Optional[discord.Guild]) -> None:
        if AUTO_ROLE_ID is None or guild is None:
            return
        try:
            user = self._ensure_user(user_id)
            total = len(user.get("stickers", []))
            capacity = int(user.get("capacity", DEFAULT_CAPACITY))
            if total >= capacity:
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
            logger.exception("_maybe_award_role: unexpected error")

    async def _ephemeral_reply(self, ctx: commands.Context, content: str, *, mention_author: bool = False) -> None:
        try:
            if getattr(ctx, "interaction", None) and getattr(ctx.interaction, "response", None) and not ctx.interaction.response.is_done():
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

    async def _grant_buildable_completion_role(self, user_id: int, buildable_key: str, guild: Optional[discord.Guild], channel: Optional[discord.TextChannel] = None) -> bool:
        """
        Attempt to grant the configured completion role. Returns True if role present or granted.
        """
        if guild is None:
            return False
        build_def = self._buildables_def.get(buildable_key, {}) or {}
        role_id = build_def.get("role_on_complete") or AUTO_ROLE_ID
        if not role_id:
            return False
        try:
            role = guild.get_role(int(role_id))
        except Exception:
            role = None
        try:
            member = guild.get_member(user_id)
        except Exception:
            member = None
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except Exception:
                member = None

        if role is None or member is None:
            logger.debug("_grant_buildable_completion_role: role or member missing (role=%s member=%s)", role, member)
            return False

        if role in member.roles:
            # ensure persistent flag is set
            try:
                rec = self._ensure_user(user_id)
                brec = rec.setdefault("buildables", {}).setdefault(buildable_key, {})
                if not brec.get("role_granted"):
                    brec["role_granted"] = True
                    await self._save()
            except Exception:
                logger.exception("_grant_buildable_completion_role: failed to persist role_granted")
            return True

        bot_member = guild.me
        if not bot_member or not bot_member.guild_permissions.manage_roles:
            logger.warning("_grant_buildable_completion_role: cannot grant role %s in guild %s (missing perms)", role_id, guild.id)
            return False
        try:
            if role.position >= (bot_member.top_role.position if bot_member.top_role else -1):
                logger.warning("_grant_buildable_completion_role: role %s is equal/above bot top role", role_id)
                return False
        except Exception:
            logger.exception("_grant_buildable_completion_role: failed role position check")

        try:
            await member.add_roles(role, reason=f"{buildable_key} completed")
            rec = self._ensure_user(user_id)
            brec = rec.setdefault("buildables", {}).setdefault(buildable_key, {})
            brec["role_granted"] = True
            await self._save()
            # announce
            try:
                post_chan = channel if channel and getattr(channel, "guild", None) else (guild.system_channel if getattr(guild, "system_channel", None) else None)
                if post_chan:
                    await post_chan.send(f"🎉 {member.mention} has completed **{buildable_key}** and was awarded {role.mention}!")
            except Exception:
                logger.exception("_grant_buildable_completion_role: announce failed")
            return True
        except Exception:
            logger.exception("_grant_buildable_completion_role: add_roles failed")
            return False

    # Keep persisted role_granted flags in sync if role removed manually
    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        try:
            removed = {r.id for r in getattr(before, "roles", [])} - {r.id for r in getattr(after, "roles", [])}
            if not removed:
                return
            uid = after.id
            changed = False
            for bk, bdef in (self._buildables_def or {}).items():
                rid = bdef.get("role_on_complete") or AUTO_ROLE_ID
                if not rid:
                    continue
                try:
                    if int(rid) in removed:
                        rec = self._ensure_user(uid)
                        brec = rec.get("buildables", {}).get(bk)
                        if brec and brec.get("role_granted"):
                            brec["role_granted"] = False
                            changed = True
                            logger.info("on_member_update: cleared role_granted for %s buildable %s", uid, bk)
                except Exception:
                    logger.exception("on_member_update: processing failed for buildable %s / member %s", bk, uid)
            if changed:
                await self._save()
        except Exception:
            logger.exception("on_member_update: unexpected error")

async def setup(bot: commands.Bot):
    await bot.add_cog(StockingCog(bot))