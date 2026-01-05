#!/usr/bin/env python3
"""
StockingCog - refactored full cog

Copy-paste this file to cogs/stocking_cog.py (replace your existing cog).
Restart/reload the bot after saving. This cog:
 - Uses data/stockings.json (self._data) as the canonical source for parts/leaderboard.
 - Provides /mysnowman and rumble_builds_leaderboard (hybrid), plus debug prefix commands.
 - Normalizes parts, sets completed/completed_at on award, and runs a startup integrity check.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

import discord
from discord import app_commands
from discord.ext import commands
from discord.utils import utcnow

# Reuse puzzle UI views for consistent leaderboard UI/behavior
from ui.views import open_leaderboard_view, LeaderboardView

logger = logging.getLogger(__name__)

# Paths
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

# Theme helpers (optional)
try:
    from utils.snowman_theme import DEFAULT_COLOR, generate_part_maps_from_buildables
except Exception:
    DEFAULT_COLOR = 0x2F3136

    def generate_part_maps_from_buildables():
        return ({}, {})

PART_EMOJI, PART_COLORS = generate_part_maps_from_buildables()

# Optional renderer plugin
try:
    from ui.stocking_render_helpers import render_stocking_image_auto
except Exception:
    render_stocking_image_auto = None


class StockingCog(commands.Cog, name="StockingCog"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._data: Dict[str, Dict[str, Any]] = {}
        self._stickers_def: Dict[str, Any] = {}
        self._buildables_def: Dict[str, Any] = {}
        self._load_all()
        logger.info("StockingCog initialized (data keys sample=%s)", list(self._data.keys())[:5])

    # -------------------------
    # Persistence
    # -------------------------
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

        # buildables (create default snowman if missing)
        try:
            if BUILDABLES_DEF_FILE.exists():
                with BUILDABLES_DEF_FILE.open("r", encoding="utf-8") as fh:
                    self._buildables_def = json.load(fh) or {}
            else:
                self._buildables_def = {
                    "snowman": {
                        "base": "buildables/snowman/base.png",
                        "parts": {
                            "carrot": {"file": "buildables/snowman/parts/carrot.png"},
                            "hat": {"file": "buildables/snowman/parts/hat.png"},
                            "scarf": {"file": "buildables/snowman/parts/scarf.png"},
                            "eyes": {"file": "buildables/snowman/parts/eyes.png"},
                            "mouth": {"file": "buildables/snowman/parts/mouth.png"},
                            "buttons": {"file": "buildables/snowman/parts/buttons.png"},
                            "arms": {"file": "buildables/snowman/parts/arms.png"},
                        },
                        "capacity_slots": 7,
                        "role_on_complete": None,
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

        # --- Data integrity: normalize stored parts and set completed flags if they already have all parts
        try:
            changed = False
            ts = None
            for uid_str, rec in (self._data or {}).items():
                buildables_rec = rec.get("buildables", {}) or {}
                for bkey, bdef in (self._buildables_def or {}).items():
                    brec = buildables_rec.get(bkey) or {}
                    parts = brec.get("parts", []) or []
                    # normalize parts to lowercase, unique
                    parts_norm = []
                    seen = set()
                    for p in parts:
                        pl = str(p).lower()
                        if pl not in seen:
                            seen.add(pl)
                            parts_norm.append(pl)
                    if parts_norm != parts:
                        brec["parts"] = parts_norm
                        buildables_rec[bkey] = brec
                        changed = True

                    # completion check: mark completed if they have all defined parts
                    parts_def = (bdef.get("parts", {}) or {})
                    defined_keys = [k for k in parts_def.keys()]
                    if defined_keys:
                        parts_set = {p.lower() for p in brec.get("parts", [])}
                        missing = [p for p in defined_keys if p.lower() not in parts_set]
                        if not missing and not brec.get("completed"):
                            brec["completed"] = True
                            if not brec.get("completed_at"):
                                if ts is None:
                                    try:
                                        ts = utcnow().isoformat()
                                    except Exception:
                                        ts = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()
                                brec["completed_at"] = ts
                            buildables_rec[bkey] = brec
                            changed = True

            if changed:
                # persist changes (async if running)
                try:
                    import asyncio as _asyncio
                    loop = _asyncio.get_event_loop()
                    if loop and loop.is_running():
                        loop.create_task(self._save())
                    else:
                        with STOCKINGS_FILE.open("w", encoding="utf-8") as fh:
                            json.dump(self._data, fh, ensure_ascii=False, indent=2)
                except Exception:
                    with STOCKINGS_FILE.open("w", encoding="utf-8") as fh:
                        json.dump(self._data, fh, ensure_ascii=False, indent=2)
        except Exception:
            logger.exception("_load_all: integrity check failed")

    async def _save(self) -> None:
        async with _save_lock:
            try:
                STOCKINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
                with STOCKINGS_FILE.open("w", encoding="utf-8") as fh:
                    json.dump(self._data, fh, ensure_ascii=False, indent=2)
                logger.debug("_save: wrote %s", STOCKINGS_FILE)
            except Exception:
                logger.exception("Failed to save stockings data")

    # -------------------------
    # Utilities
    # -------------------------
    def _ensure_user(self, user_id: int) -> Dict[str, Any]:
        key = str(user_id)
        if key not in self._data:
            self._data[key] = {"stickers": [], "capacity": DEFAULT_CAPACITY, "buildables": {}}
        return self._data[key]

    def get_user_stocking(self, user_id: int) -> Dict[str, Any]:
        return self._ensure_user(user_id)

    def _format_collected_list(self, parts: List[str], max_len: int = 750) -> str:
        """Compact representation for collected / missing lists."""
        if not parts:
            return "(none)"
        try:
            parts_sorted = sorted(parts, key=lambda x: int(x) if str(x).isdigit() else x)
        except Exception:
            parts_sorted = list(parts)
        if all(str(p).isdigit() for p in parts_sorted):
            s = ", ".join(str(int(p)) for p in parts_sorted)
        else:
            out = []
            for p in parts_sorted:
                try:
                    em = PART_EMOJI.get(p.lower()) if isinstance(PART_EMOJI, dict) else None
                except Exception:
                    em = None
                out.append(em if em else str(p))
            s = ", ".join(out)
        if len(s) > max_len:
            s = s[: max_len - 2].rstrip() + " …"
        return s

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
                await asyncio.sleep(0.4)
                await channel.send(f"🎉 {mention} earned a **{sticker_key}** sticker! Use `/mysnowman` to view your snowman.")
            except Exception:
                logger.exception("award_sticker: failed to announce sticker award")
        try:
            await self._maybe_award_role(user_id, channel.guild if channel is not None else None)
        except Exception:
            logger.exception("award_sticker: maybe_award_role failed")
        return True

    async def award_part(self, user_id: int, buildable_key: str, part_key: str,
                         channel: Optional[discord.TextChannel] = None, *, announce: bool = True) -> bool:
        """Persist a part award and announce. Ensure role_granted/completed are consistent."""
        build_def = self._buildables_def.get(buildable_key)
        if not build_def:
            logger.warning("award_part: unknown buildable %s", buildable_key)
            return False
        parts_def = build_def.get("parts", {}) or {}
        if part_key not in parts_def:
            logger.warning("award_part: unknown part %s for %s", part_key, buildable_key)
            return False

        user = self._ensure_user(user_id)
        brec = user.setdefault("buildables", {}).setdefault(buildable_key, {"parts": [], "completed": False})

        # if user already has the part (case-insensitive), skip announcement
        try:
            existing = {str(p).lower() for p in brec.get("parts", [])}
            if str(part_key).strip().lower() in existing:
                logger.info("award_part: user %s already has %s for %s", user_id, part_key, buildable_key)
                if announce and channel:
                    try:
                        member = channel.guild.get_member(user_id) if channel and channel.guild else None
                        mention = member.mention if member else f"<@{user_id}>"
                        await asyncio.sleep(0.4)
                        await channel.send(f"{mention} already has the **{part_key}** for {buildable_key}.")
                    except Exception:
                        logger.exception("award_part: failed to announce already-has")
                return False
        except Exception:
            logger.exception("award_part: checking existing parts failed")

        # normalize and persist award (store lowercase keys, keep uniqueness)
        try:
            parts_list = brec.setdefault("parts", [])
            new_part = str(part_key).strip()
            # avoid duplicate (case-insensitive)
            if new_part.lower() not in {str(p).lower() for p in parts_list}:
                parts_list.append(new_part)
            # normalize stored parts to lowercase unique list
            normalized = []
            seen = set()
            for p in parts_list:
                pl = str(p).lower()
                if pl not in seen:
                    seen.add(pl)
                    normalized.append(pl)
            brec["parts"] = normalized

            # recompute completion and set completed_at if needed
            try:
                capacity_slots = int(build_def.get("capacity_slots", len(parts_def)))
            except Exception:
                capacity_slots = len(parts_def)
            total = len(brec.get("parts", []) or [])
            if total >= capacity_slots or total >= len(parts_def):
                if not brec.get("completed"):
                    brec["completed"] = True
                    try:
                        brec["completed_at"] = utcnow().isoformat()
                    except Exception:
                        brec["completed_at"] = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()

            # persist canonical changes to stockings.json
            await self._save()
        except Exception:
            logger.exception("award_part: normalization/persist step failed")

        # persist into bot.data model as well (user_pieces/buildables)
        try:
            botdata = getattr(self.bot, "data", None)
            if botdata is None:
                botdata = {}
                setattr(self.bot, "data", botdata)
            botdata.setdefault("user_pieces", {})
            up = botdata["user_pieces"]
            uid_str = str(user_id)
            up.setdefault(uid_str, {})
            existing_parts = {str(x).lower() for x in up[uid_str].get(buildable_key, [])}
            for p in brec.get("parts", []) or []:
                existing_parts.add(str(p).lower())
            up[uid_str][buildable_key] = list(existing_parts)

            # ensure buildables metadata present in botdata
            botdata.setdefault("buildables", {})
            try:
                if self._buildables_def:
                    # merge without overwriting existing keys
                    for k, v in (self._buildables_def or {}).items():
                        if k not in botdata["buildables"]:
                            botdata["buildables"][k] = v
            except Exception:
                logger.exception("award_part: merging buildables_def into bot.data failed")

            # prefer utils.db_utils.save_data if available
            try:
                from utils.db_utils import save_data
                save_data(botdata)
            except Exception:
                # fallback: write to data/collected_pieces.json
                collected_file = DATA_DIR / "collected_pieces.json"
                try:
                    with collected_file.open("w", encoding="utf-8") as fh:
                        json.dump(botdata, fh, ensure_ascii=False, indent=2)
                except Exception:
                    logger.exception("award_part: failed to persist bot.data fallback file")
        except Exception:
            logger.exception("award_part: failed to persist into bot.data model")

        # attempt to render composite (best-effort)
        try:
            _ = await self.render_buildable(user_id, buildable_key)
        except Exception:
            pass

        # announce award (short embed)
        if announce and channel:
            member = None
            display = None
            try:
                if channel and getattr(channel, "guild", None):
                    member = channel.guild.get_member(user_id)
                    if member is None:
                        try:
                            member = await channel.guild.fetch_member(user_id)
                        except Exception:
                            member = None
                    if member:
                        display = getattr(member, "display_name", None) or getattr(member, "name", None)
                if not display:
                    try:
                        u = await self.bot.fetch_user(user_id)
                        display = getattr(u, "display_name", None) or getattr(u, "name", None)
                    except Exception:
                        display = None
            except Exception:
                logger.exception("award_part: error resolving display name / member")

            title = f"☃️ Congratulations, {display}! ☃️" if display else "☃️ Congratulations! ☃️"
            emoji = PART_EMOJI.get(part_key.lower(), "")
            color_int = PART_COLORS.get(part_key.lower(), DEFAULT_COLOR)
            color = discord.Color(color_int if isinstance(color_int, int) else DEFAULT_COLOR)
            desc = f"You've been awarded the {part_key} for your {buildable_key}! {emoji}"
            emb = discord.Embed(title=title, description=desc, color=color)
            try:
                mention_content = member.mention if member else f"<@{user_id}>"
            except Exception:
                mention_content = f"<@{user_id}>"
            try:
                await channel.send(content=mention_content, embed=emb)
                logger.info("award_part: announced %s to channel %s for user %s", part_key, getattr(channel, "id", None), user_id)
            except Exception:
                logger.exception("award_part: failed to announce award")

        # completion post-processing: grant role if configured
        try:
            if brec.get("completed"):
                role_id = build_def.get("role_on_complete") or AUTO_ROLE_ID
                guild = channel.guild if channel and getattr(channel, "guild", None) else None
                if not guild:
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
                                # persist role_granted flag
                                try:
                                    rec = self._ensure_user(user_id)
                                    brec2 = rec.setdefault("buildables", {}).setdefault(buildable_key, {})
                                    brec2["role_granted"] = True
                                    brec2["completed"] = True
                                    if not brec2.get("completed_at"):
                                        try:
                                            brec2["completed_at"] = utcnow().isoformat()
                                        except Exception:
                                            brec2["completed_at"] = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()
                                    await self._save()
                                except Exception:
                                    logger.exception("award_part: failed to persist role_granted flag")
                                # announce completion
                                try:
                                    if channel and getattr(channel, "guild", None):
                                        await asyncio.sleep(0.4)
                                        await channel.send(embed=discord.Embed(
                                            title=f"{buildable_key} Completed!",
                                            description=f"🎉 {member.mention} completed **{buildable_key}** and was awarded {role.mention}!",
                                            color=discord.Color.green()))
                                except Exception:
                                    logger.exception("award_part: failed to announce completion")
                    except Exception:
                        logger.exception("award_part: role grant flow failed")
        except Exception:
            logger.exception("award_part: completion post-processing failed")

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
        """Render composite PNG for a user's buildable. Returns path or None."""
        # Safer plugin handling: plugin may be sync, async, or return None
        if render_stocking_image_auto:
            try:
                maybe = render_stocking_image_auto(self._data, user_id, buildable_key, ASSETS_DIR)
                if inspect.isawaitable(maybe):
                    out = await maybe
                else:
                    out = maybe
                if out:
                    return Path(out)
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
    @commands.hybrid_command(name="mysnowman", description="Show your snowman assembled from collected parts.")
    async def mysnowman(self, ctx: commands.Context):
        user = ctx.author
        user_id = getattr(user, "id", None)
        if not user_id:
            await self._ephemeral_reply(ctx, "Could not determine your user id.")
            return

        build_key = "snowman"
        build_def = self._buildables_def.get(build_key)
        if not build_def:
            await self._ephemeral_reply(ctx, "No snowman buildable configured.")
            return

        rec = self._ensure_user(user_id)
        b = rec.get("buildables", {}).get(build_key, {"parts": [], "completed": False})
        user_parts = list(dict.fromkeys(b.get("parts", []) or []))
        parts_def = build_def.get("parts", {}) or {}
        all_parts = list(parts_def.keys())
        capacity_slots = int(build_def.get("capacity_slots", len(all_parts)))

        is_complete = bool(b.get("completed")) or (len(user_parts) >= capacity_slots or len(user_parts) >= len(all_parts))
        if is_complete:
            try:
                await self._grant_buildable_completion_role(user_id, build_key, ctx.guild, ctx.channel)
            except Exception:
                logger.exception("mysnowman: error while attempting to grant completion role for user %s", user_id)

        composite_path = None
        try:
            composite_path = await self.render_buildable(user_id, build_key)
        except Exception:
            composite_path = None

        try:
            embed_color = discord.Color(DEFAULT_COLOR) if isinstance(DEFAULT_COLOR, int) else (DEFAULT_COLOR or discord.Color.dark_blue())
        except Exception:
            embed_color = discord.Color.dark_blue()

        title = "☃️ Snowman ☃️"
        embed = discord.Embed(title=title, color=embed_color, timestamp=discord.utils.utcnow())

        def _emoji_or_name(p: str) -> str:
            try:
                e = PART_EMOJI.get(p.lower()) if isinstance(PART_EMOJI, dict) else None
                if str(p).isdigit():
                    return str(p)
                return e if e else p
            except Exception:
                return p

        collected_items = [_emoji_or_name(p) for p in user_parts]
        missing_parts = [p for p in all_parts if p not in user_parts]
        missing_items = [_emoji_or_name(p) for p in missing_parts]

        collected_line = " ".join(collected_items) if collected_items else "(none)"
        missing_line = " ".join(missing_items) if missing_items else "(none)"

        embed.add_field(name="Collected", value=collected_line, inline=False)
        embed.add_field(name="Missing", value=missing_line, inline=False)

        if composite_path and composite_path.exists():
            try:
                file = discord.File(composite_path, filename=composite_path.name)
                embed.set_image(url=f"attachment://{composite_path.name}")
                await ctx.reply(embed=embed, file=file, mention_author=False)
                return
            except Exception:
                logger.exception("mysnowman: failed to send composite image, falling back")

        candidate = None
        base_rel = build_def.get("base")
        if base_rel:
            base_path = Path(base_rel)
            if not base_path.exists():
                base_path = ASSETS_DIR / base_rel
            if not base_path.exists():
                base_path = ROOT / base_rel
            if base_path.exists():
                candidate = base_path

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
                f = discord.File(candidate, filename=candidate.name)
                embed.set_image(url=f"attachment://%s" % candidate.name)
                await ctx.reply(embed=embed, file=f, mention_author=False)
                return
            except Exception:
                logger.exception("mysnowman: failed to send fallback image %s", candidate)

        try:
            await ctx.reply(embed=embed, mention_author=False)
        except Exception:
            await self._ephemeral_reply(ctx, f"You have {len(user_parts)} parts: {', '.join(user_parts) if user_parts else '(none)'}.")

    # -------------------------
    # Leaderboard command (now uses the same LeaderboardView used by the puzzles cog)
    @commands.hybrid_command(
        name="rumble_builds_leaderboard",
        aliases=["sled", "stocking_leaderboard", "stockingboard"],
        description="Show stocking leaderboard for this guild (default: snowman)."
    )
    @commands.guild_only()
    @app_commands.describe(buildable="Which buildable to inspect (defaults to 'snowman')")
    async def rumble_builds_leaderboard(self, ctx: commands.Context, buildable: Optional[str] = "snowman"):
        await ctx.defer(ephemeral=False)

        buildable = (buildable or "snowman").strip()
        build_def = (self._buildables_def or {}).get(buildable, {}) or {}
        parts_def = build_def.get("parts", {}) or {}

        # Debug log to confirm which data source we'll inspect
        logger.info("LB RUN: buildable=%s guild=%s persisted_users=%d has_botdata=%s",
                    buildable, getattr(ctx.guild, "id", None), len(self._data or {}), bool(getattr(self.bot, "data", None)))

        interaction = getattr(ctx, "interaction", None)
        if interaction:
            # Use the standard open_leaderboard_view for interaction (same UX as puzzles)
            try:
                return await open_leaderboard_view(self.bot, interaction, buildable)
            except Exception:
                logger.exception("rumble_builds_leaderboard: open_leaderboard_view failed, falling back to non-interaction path")

        # Build leaderboard_data: list of (uid:int, count:int)
        leaderboard_map: Dict[int, int] = {}

        # Prefer persisted stockings.json entries
        try:
            for uid_str, rec in (self._data or {}).items():
                try:
                    uid = int(uid_str)
                except Exception:
                    continue
                brec = ((rec.get("buildables") or {}).get(buildable) or {})
                parts = brec.get("parts", []) or []
                if parts:
                    leaderboard_map[uid] = max(leaderboard_map.get(uid, 0), len(parts))
        except Exception:
            logger.exception("rumble_builds_leaderboard: error reading self._data")

        # Fallback: runtime bot.data.user_pieces (if no persisted data found for those users)
        if not leaderboard_map:
            try:
                all_user_pieces = (getattr(self.bot, "data", {}) or {}).get("user_pieces", {}) or {}
                for user_id_str, user_puzzles in all_user_pieces.items():
                    try:
                        uid = int(user_id_str)
                    except Exception:
                        continue
                    parts = (user_puzzles or {}).get(buildable, []) or []
                    if parts:
                        leaderboard_map[uid] = max(leaderboard_map.get(uid, 0), len(parts))
            except Exception:
                logger.exception("rumble_builds_leaderboard: error reading bot.data.user_pieces")

        leaderboard_data = [(uid, cnt) for uid, cnt in leaderboard_map.items() if cnt > 0]
        leaderboard_data.sort(key=lambda x: (-x[1], x[0]))

        if not leaderboard_data:
            await ctx.reply("No stocking data found for this buildable.", mention_author=False)
            return

        try:
            view = LeaderboardView(self.bot, ctx.guild, buildable, leaderboard_data, page=0)
            embed = await view.generate_embed()
            # Reuse _reply pattern used elsewhere: use ctx.reply directly here
            await ctx.reply(embed=embed, view=view, mention_author=False)
        except Exception:
            logger.exception("rumble_builds_leaderboard: failed to build/render LeaderboardView, falling back to simple list")
            # Fallback simple textual listing
            lines = []
            for rank, (uid, cnt) in enumerate(leaderboard_data, start=1):
                try:
                    user = self.bot.get_user(uid) or await self.bot.fetch_user(uid)
                    mention = user.mention
                except Exception:
                    mention = f"`{uid}`"
                lines.append(f"{rank}. {mention} — {cnt} parts")
            out = "\n".join(lines)
            await ctx.reply(f"```\n{out}\n```", mention_author=False)

    # -------------------------
    # Debug helpers (prefix commands)
    @commands.command(name="dbg_list_cog_cmds")
    @commands.has_guild_permissions(manage_guild=True)
    async def dbg_list_cog_cmds(self, ctx: commands.Context):
        """
        List commands defined on this Cog (prefix commands). Run in guild as an admin.
        """
        try:
            cog_cmds = [c.name for c in self.get_commands()] if hasattr(self, "get_commands") else []
            await ctx.reply(f"registered commands on cog: {', '.join(cog_cmds) if cog_cmds else '(none)'}", mention_author=False)
        except Exception:
            logger.exception("dbg_list_cog_cmds failed")
            await self._ephemeral_reply(ctx, "Failed to list commands on cog.")

    @commands.command(name="dbg_show_parts")
    @commands.has_guild_permissions(manage_guild=True)
    async def dbg_show_parts(self, ctx: commands.Context, member_or_id: Optional[str] = None, buildable: Optional[str] = "snowman"):
        """
        Debug helper: show parts from stockings.json (self._data) and from runtime self.bot.data.user_pieces.
        Usage:
          !dbg_show_parts                  -> shows for invoking user
          !dbg_show_parts @Member          -> shows for mentioned member
          !dbg_show_parts 625759569578164244 -> show for explicit id
          Optionally add a buildable name as second arg (defaults to snowman).
        """
        import re

        try:
            guild = ctx.guild
            # Resolve target uid
            if member_or_id is None:
                uid = getattr(ctx.author, "id", None)
            else:
                # try to extract a snowflake from a mention or raw id
                m = re.search(r"(\d{16,22})", member_or_id)
                if m:
                    uid = int(m.group(1))
                else:
                    uid = None
                    if guild:
                        member = None
                        try:
                            member = await commands.MemberConverter().convert(ctx, member_or_id)
                        except Exception:
                            member = discord.utils.find(lambda mm: (mm.name == member_or_id) or (mm.display_name == member_or_id), guild.members)
                        if member:
                            uid = member.id
            if not uid:
                await self._ephemeral_reply(ctx, "Could not resolve the target user. Provide a mention or numeric ID, or omit to use yourself.")
                return

            uid_str = str(uid)
            # stockings.json (self._data)
            stock_rec = (self._data or {}).get(uid_str) or {}
            stock_brec = ((stock_rec.get("buildables") or {}).get(buildable) or {})
            stock_parts = stock_brec.get("parts", []) or []
            stock_completed = bool(stock_brec.get("completed"))
            stock_completed_at = stock_brec.get("completed_at")

            # runtime self.bot.data.user_pieces (if present)
            botdata = getattr(self.bot, "data", {}) or {}
            up = botdata.get("user_pieces", {}) or {}
            bot_parts = (up.get(uid_str, {}) or {}).get(buildable, []) or []

            text = (
                f"stockings.json (self._data) for {uid_str} / {buildable}:\n"
                f"  parts: {stock_parts}\n"
                f"  completed: {stock_completed}\n"
                f"  completed_at: {stock_completed_at}\n\n"
                f"runtime self.bot.data.user_pieces for {uid_str} / {buildable}:\n"
                f"  parts: {bot_parts}\n"
            )
            await ctx.reply(f"```\n{text}\n```", mention_author=False)
        except Exception:
            logger.exception("dbg_show_parts failed")
            await self._ephemeral_reply(ctx, "Debug failed; see logs.")

    # optional admin command to clear runtime data
    @commands.command(name="admin_clear_runtime_data")
    @commands.is_owner()
    async def admin_clear_runtime_data(self, ctx: commands.Context):
        """Clear in-memory bot.data (use with caution)."""
        try:
            self.bot.data = {}
            await ctx.reply("Cleared bot.data runtime store.", mention_author=False)
        except Exception as e:
            logger.exception("admin_clear_runtime_data failed")
            await ctx.reply(f"Failed to clear runtime data: {e}", mention_author=False)

    # -------------------------
    # Role helpers & events
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
        Grant the configured completion role and ensure persistent flags are set.
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

        try:
            if role in member.roles:
                rec = self._ensure_user(user_id)
                brec = rec.setdefault("buildables", {}).setdefault(buildable_key, {})
                if not brec.get("role_granted"):
                    brec["role_granted"] = True
                if not brec.get("completed"):
                    brec["completed"] = True
                if not brec.get("completed_at"):
                    try:
                        brec["completed_at"] = utcnow().isoformat()
                    except Exception:
                        brec["completed_at"] = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()
                await self._save()
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

            await member.add_roles(role, reason=f"{buildable_key} completed")
            rec = self._ensure_user(user_id)
            brec = rec.setdefault("buildables", {}).setdefault(buildable_key, {})
            brec["role_granted"] = True
            brec["completed"] = True
            try:
                brec["completed_at"] = utcnow().isoformat()
            except Exception:
                brec["completed_at"] = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()
            await self._save()
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
                except Exception:
                    logger.exception("on_member_update: processing failed for buildable %s / member %s", bk, uid)
            if changed:
                await self._save()
        except Exception:
            logger.exception("on_member_update: unexpected error")


async def setup(bot: commands.Bot):
    await bot.add_cog(StockingCog(bot))