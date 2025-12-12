#!/usr/bin/env python3
"""
RumbleListenerCog — full replacement.

Features:
- Robust winner extraction (mentions, <@id>, embed fields, "Participants:" blocks, "WINNER" lines).
- Async name->member resolution with safe fetch fallback for small guilds.
- Per-channel mapping, global fallback mapping (channel id 0), and best-effort parsing.
- Awards parts via StockingCog.award_part(..., announce=False), and posts a small embed with part thumbnail.
- PART_EMOJI / PART_COLORS auto-generated from data/buildables.json.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import discord
from discord.ext import commands

logger = logging.getLogger(__name__)

DATA_DIR = Path("data")
CONFIG_FILE = DATA_DIR / "rumble_listener_config.json"
ASSETS_DIR = DATA_DIR / "stocking_assets"
SEARCH_STARTER_BACK_MESSAGES = 40

WINNER_TITLE_RE = re.compile(r"\bWINNER\b|WON!?", re.IGNORECASE)
ADDITIONAL_WIN_RE = re.compile(r"\b(found (?:a|an)|received|was awarded|winner|won)\b", re.IGNORECASE)
PART_FOR_BUILDABLE_RE = re.compile(r"(?:found|received|earned|got)\s+(?P<part>\w+)(?:\s+for\s+(?P<buildable>\w+))?", re.IGNORECASE)
STARTED_BY_RE = re.compile(
    r"Started by\s*[:\-]?\s*(?:<@!?(\d+)>|@?([A-Za-z0-9_`~\-\s]+))",
    re.IGNORECASE,
)

DEFAULT_COLOR = 0x2F3136

# Canonical small mapping for common part names; used as first preference.
_CANONICAL_EMOJI: Dict[str, str] = {
    "hat": "🎩",
    "scarf": "🧣",
    "carrot": "🥕",
    "eyes": "👀",
    "mouth": "😄",
    "buttons": "⚪",
    "arms": "✋",
}
# color choices by semantic part name (hex ints)
_CANONICAL_COLORS: Dict[str, int] = {
    "hat": 0x001F3B,  # navy
    "scarf": 0x8B0000,
    "carrot": 0xFFA500,
    "eyes": 0x9E9E9E,
    "mouth": 0x9E9E9E,
    "buttons": 0x9E9E9E,
    "arms": 0x6B4423,
}


def ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _generate_part_maps_from_buildables() -> Tuple[Dict[str, str], Dict[str, int]]:
    buildables_path = DATA_DIR / "buildables.json"
    parts_keys = set()
    try:
        if buildables_path.exists():
            data = json.loads(buildables_path.read_text(encoding="utf-8") or "{}")
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
        color = _CANONICAL_COLORS.get(key_lower, DEFAULT_COLOR)
        part_emoji[key_lower] = emoji
        part_colors[key_lower] = color

    if not part_emoji:
        for k, v in _CANONICAL_EMOJI.items():
            part_emoji[k] = v
            part_colors[k] = _CANONICAL_COLORS.get(k, DEFAULT_COLOR)

    return part_emoji, part_colors


PART_EMOJI, PART_COLORS = _generate_part_maps_from_buildables()


# --- Helpers for name normalization and matching ---------------------------
def _normalize_name(s: str) -> str:
    """
    Normalize a username/display_name or candidate string for robust matching:
    - NFKD normalization, strip diacritics,
    - lowercase,
    - remove most punctuation except internal spaces,
    - collapse whitespace.
    """
    if not s:
        return ""
    nk = unicodedata.normalize("NFKD", s)
    nk = "".join(ch for ch in nk if not unicodedata.combining(ch))
    nk = nk.lower()
    nk = re.sub(r"[^\w\s]", " ", nk)
    nk = re.sub(r"\s+", " ", nk).strip()
    return nk


def _extract_participants_block(text: str) -> List[str]:
    """
    Given a text blob, try to extract lines under 'Participants:' or 'Participants' header.
    Returns list of candidate names (raw strings).
    """
    candidates: List[str] = []
    if not text:
        return candidates
    m = re.search(r"participants\s*[:\-]?\s*(?::)?", text, re.IGNORECASE)
    if not m:
        return candidates
    start = m.end()
    tail = text[start:]
    lines = tail.splitlines()
    for ln in lines:
        ln = ln.strip()
        if not ln:
            break
        ln_clean = ln.lstrip("•-@").strip()
        ln_clean = re.sub(r"^[^\w@#@]+", "", ln_clean).strip()
        candidates.append(ln_clean)
        if len(candidates) >= 50:
            break
    return candidates


class RumbleListenerCog(commands.Cog):
    def __init__(self, bot: commands.Bot, initial_config: Optional[Dict[str, Any]] = None):
        self.bot = bot
        self._lock = asyncio.Lock()
        ensure_data_dir()
        self.rumble_bot_ids: List[int] = []
        self.channel_part_map: Dict[int, Tuple[str, str]] = {}
        if initial_config:
            self._load_from_dict(initial_config)
        self._load_config_file()

    # config persistence
    def _load_from_dict(self, data: Dict[str, Any]) -> None:
        rids = data.get("rumble_bot_ids", [])
        self.rumble_bot_ids = [int(x) for x in rids] if rids else []
        cmap = data.get("channel_part_map", {})
        new_map: Dict[int, Tuple[str, str]] = {}
        for chk, val in cmap.items():
            try:
                ch = int(chk)
            except Exception:
                continue
            if isinstance(val, (list, tuple)) and len(val) >= 2:
                new_map[ch] = (str(val[0]), str(val[1]))
        self.channel_part_map = new_map

    def _load_config_file(self) -> None:
        try:
            if CONFIG_FILE.exists():
                with CONFIG_FILE.open("r", encoding="utf-8") as fh:
                    data = json.load(fh)
                    if isinstance(data, dict):
                        self._load_from_dict(data)
        except Exception:
            logger.exception("rumble_listener: failed to load config file")

    def _save_config_file(self) -> None:
        try:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            out = {
                "rumble_bot_ids": self.rumble_bot_ids,
                "channel_part_map": {str(k): [v[0], v[1]] for k, v in self.channel_part_map.items()},
            }
            with CONFIG_FILE.open("w", encoding="utf-8") as fh:
                json.dump(out, fh, ensure_ascii=False, indent=2)
        except Exception:
            logger.exception("rumble_listener: failed to save config file")

    @staticmethod
    def _extract_ids_from_text(text: str) -> List[int]:
        ids: List[int] = []
        if not text:
            return ids
        raw_ids = re.findall(r"<@!?(?P<id>\d+)>", text)
        for r in raw_ids:
            try:
                ids.append(int(r))
            except Exception:
                pass
        return ids

    def _collect_candidate_names(self, message: discord.Message) -> List[str]:
        """
        Scan message.content and embeds for candidate plain-text names.
        """
        candidates: List[str] = []
        content = message.content or ""
        # content lines heuristics
        lines = content.splitlines()
        for i, ln in enumerate(lines):
            ln = ln.strip()
            if not ln:
                continue
            if WINNER_TITLE_RE.search(ln):
                if i + 1 < len(lines):
                    cand = lines[i + 1].strip()
                    if cand:
                        candidates.append(cand)
            m = re.search(r"WINNER[^\w]*(?P<name>[\w\W]{1,64})", ln, re.IGNORECASE)
            if m:
                name = m.group("name").strip()
                if name:
                    candidates.append(name)

        # embed scanning
        try:
            for emb in message.embeds:
                emb_text = " ".join(filter(None, [emb.title or "", emb.description or ""] + [f.value for f in (emb.fields or [])]))
                for f in (emb.fields or []):
                    if WINNER_TITLE_RE.search(f.name or "") or WINNER_TITLE_RE.search(f.value or ""):
                        for line in (f.value or "").splitlines():
                            line = line.strip()
                            if line:
                                candidates.append(line)
                                break
                if emb.title and WINNER_TITLE_RE.search(emb.title):
                    if emb.description:
                        for line in emb.description.splitlines():
                            line = line.strip()
                            if line:
                                candidates.append(line)
                                break
                if "participants" in emb_text.lower():
                    pc = _extract_participants_block(emb_text)
                    candidates.extend(pc)
        except Exception:
            logger.exception("rumble_listener: embed parsing failed")

        candidates.extend(_extract_participants_block(content))

        # ranking lists
        try:
            for ln in content.splitlines():
                ln = ln.strip()
                if re.match(r"^\s*\d+\.", ln) or re.match(r"^[\u2022\-\*]\s*", ln):
                    ln_clean = re.sub(r"^(?:\d+\.\s*|[\u2022\-\*]\s*)", "", ln)
                    if ln_clean:
                        candidates.append(ln_clean.strip())
        except Exception:
            pass

        # dedupe preserve order
        seen = set()
        out = []
        for c in candidates:
            c = c.strip()
            if not c:
                continue
            if c not in seen:
                seen.add(c)
                out.append(c)
            if len(out) >= 50:
                break
        return out

    async def _match_names_to_member_ids(self, guild: discord.Guild, candidates: List[str]) -> List[int]:
        """
        Async name->member resolution. Will try cached members first, then (only if guild is small)
        fetch members from the API as a fallback.
        """
        if not guild or not candidates:
            return []

        members = list(guild.members or [])
        norm_map: Dict[int, Tuple[str, str]] = {}
        for m in members:
            try:
                norm_map[m.id] = (_normalize_name(m.name or ""), _normalize_name(m.display_name or ""))
            except Exception:
                norm_map[m.id] = ((m.name or "").lower(), (m.display_name or "").lower())

        async def _try_match(norm_map_local: Dict[int, Tuple[str, str]]) -> List[int]:
            out: List[int] = []
            for cand in candidates:
                nc = _normalize_name(cand)
                if not nc:
                    continue
                found = None
                for mid, (nname, dname) in norm_map_local.items():
                    try:
                        if nname and nname == nc:
                            found = mid
                            break
                        if dname and dname == nc:
                            found = mid
                            break
                    except Exception:
                        continue
                if found:
                    out.append(found)
                    continue
                for mid, (nname, dname) in norm_map_local.items():
                    try:
                        if nname and (nc in nname or nname in nc):
                            found = mid
                            break
                        if dname and (nc in dname or dname in nc):
                            found = mid
                            break
                    except Exception:
                        continue
                if found:
                    if found not in out:
                        out.append(found)
                    continue
                tokens = nc.split()
                if tokens:
                    t0 = tokens[0]
                    for mid, (nname, dname) in norm_map_local.items():
                        try:
                            if (nname and nname.startswith(t0)) or (dname and dname.startswith(t0)):
                                if mid not in out:
                                    out.append(mid)
                                    break
                        except Exception:
                            continue
            return out

        matched_ids = await _try_match(norm_map)
        # If nothing matched and guild is small-ish, attempt to fetch members
        if not matched_ids:
            try:
                if guild.member_count and guild.member_count <= 1000:
                    logger.info("rumble_listener: fetching members for guild %s to resolve names (count=%s)", guild.id, guild.member_count)
                    fetched = []
                    async for m in guild.fetch_members(limit=None):
                        fetched.append(m)
                    norm_map = {m.id: (_normalize_name(m.name or ""), _normalize_name(m.display_name or "")) for m in fetched}
                    matched_ids = await _try_match(norm_map)
            except Exception:
                logger.exception("rumble_listener: failed to fetch members for name resolution")

        seen = set()
        out_final = []
        for mid in matched_ids:
            if mid not in seen:
                seen.add(mid)
                out_final.append(mid)
        return out_final

    async def _extract_winner_ids(self, message: discord.Message) -> List[int]:
        """
        Robust async winner ID extraction that:
        - Prefers explicit mentions on the same message
        - Falls back to scanning recent prior messages for mentions (useful when embed follows a mention)
        - Scans content and embeds for <@id> patterns
        - If necessary, extracts plain-text candidate names from WINNER fields and resolves them to member IDs
        """
        # 1) explicit mentions on the message (best)
        try:
            if message.mentions:
                return [m.id for m in message.mentions]
        except Exception:
            pass

        # 2) look for explicit mention tokens (<@12345>) in the message content or embed text
        try:
            ids = []
            if message.content:
                ids.extend(re.findall(r"<@!?(?P<id>\d+)>", message.content))
            for emb in message.embeds:
                text = " ".join(
                    filter(None, [emb.title or "", emb.description or ""] + [f.value for f in (emb.fields or [])]))
                ids.extend(re.findall(r"<@!?(?P<id>\d+)>", text))
            ids = [int(x) for x in dict.fromkeys(ids)]  # dedupe preserving order
            if ids:
                return ids
        except Exception:
            pass

        # 3) If we didn't find direct mentions, scan recent prior messages for mentions.
        #    Embed often follows a short plain-text message that contains the ping.
        try:
            async for prev in message.channel.history(limit=8, before=message.created_at, oldest_first=False):
                if prev.mentions:
                    return [m.id for m in prev.mentions]
                # also check textual <@id> in prior message
                if prev.content:
                    m_ids = re.findall(r"<@!?(?P<id>\d+)>", prev.content)
                    if m_ids:
                        return [int(m_ids[0])]
        except Exception:
            pass

        # 4) No explicit mentions nearby. Try to extract candidate names from embed WINNER fields / description.
        candidates = []
        try:
            # look for fields or descriptions that include the word WINNER or the WINNER art block followed by a name line
            for emb in message.embeds:
                # fields with "WINNER" in name or value
                for f in (emb.fields or []):
                    if WINNER_TITLE_RE.search(f.name or "") or WINNER_TITLE_RE.search(f.value or ""):
                        # take first non-empty line from the value as candidate
                        for ln in (f.value or "").splitlines():
                            ln = ln.strip()
                            if ln:
                                candidates.append(ln)
                                break
                # check description/title for "WINNER" followed by a line
                if emb.title and WINNER_TITLE_RE.search(emb.title) and emb.description:
                    for ln in emb.description.splitlines():
                        ln = ln.strip()
                        if ln:
                            candidates.append(ln)
                            break
                # fallback: sometimes the winner name is present as a short line in description
                if emb.description:
                    # look for lines under "WINNER" keyword in the description
                    lines = emb.description.splitlines()
                    for i, ln in enumerate(lines):
                        if WINNER_TITLE_RE.search(ln):
                            if i + 1 < len(lines):
                                cand = lines[i + 1].strip()
                                if cand:
                                    candidates.append(cand)
        except Exception:
            pass

        # dedupe candidates in order
        seen = set()
        candidates = [c for c in candidates if c and not (c in seen or seen.add(c))]

        if not candidates:
            return []

        # 5) resolve candidate plain-text names to member IDs (async helper will try cached members then fetch if allowed)
        try:
            guild = message.guild
            if not guild:
                return []
            matched = await self._match_names_to_member_ids(guild, candidates)
            return matched
        except Exception:
            return []

    # listener
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None:
            return
        try:
            author_id = int(message.author.id)
        except Exception:
            return

        # If rumble_bot_ids configured, only accept messages from them or preceded by them
        if self.rumble_bot_ids:
            if author_id not in self.rumble_bot_ids:
                found_recent_rumble = False
                try:
                    async for prev in message.channel.history(limit=6, before=message.created_at, oldest_first=False):
                        try:
                            if int(prev.author.id) in self.rumble_bot_ids:
                                found_recent_rumble = True
                                break
                        except Exception:
                            continue
                except Exception:
                    found_recent_rumble = False
                if not found_recent_rumble:
                    return

        # Detect a winner-style message
        found = False
        if message.content and WINNER_TITLE_RE.search(message.content):
            found = True
        if not found and message.content and ADDITIONAL_WIN_RE.search(message.content):
            found = True
        for emb in message.embeds:
            if (emb.title and (WINNER_TITLE_RE.search(emb.title) or ADDITIONAL_WIN_RE.search(emb.title))) or \
               (emb.description and (WINNER_TITLE_RE.search(emb.description) or ADDITIONAL_WIN_RE.search(emb.description))):
                found = True
                break
            for f in (emb.fields or []):
                if WINNER_TITLE_RE.search(f.name or "") or WINNER_TITLE_RE.search(f.value or "") or \
                   ADDITIONAL_WIN_RE.search(f.name or "") or ADDITIONAL_WIN_RE.search(f.value or ""):
                    found = True
                    break
            if found:
                break
        if not found:
            return

        # Async extraction (may fetch members)
        try:
            winner_ids = await self._extract_winner_ids(message)
        except Exception:
            logger.exception("rumble_listener: winner id extraction failed")
            winner_ids = []

        if not winner_ids:
            return

        # Resolve mapping (channel-specific then global '0')
        mapping = self.channel_part_map.get(message.channel.id)
        if not mapping:
            mapping = self.channel_part_map.get(0) or self.channel_part_map.get("0")
            if not mapping:
                return

        buildable_key, part_key = mapping
        stocking_cog = self.bot.get_cog("StockingCog")
        if stocking_cog is None:
            return

        async with self._lock:
            for wid in winner_ids:
                try:
                    awarded = False
                    if hasattr(stocking_cog, "award_part"):
                        awarded = await getattr(stocking_cog, "award_part")(int(wid), buildable_key, part_key, None, announce=False)
                    elif hasattr(stocking_cog, "award_sticker"):
                        awarded = await getattr(stocking_cog, "award_sticker")(int(wid), part_key, None, announce=False)
                    if not awarded:
                        continue

                    member = message.guild.get_member(int(wid))
                    mention = member.mention if member else f"<@{wid}>"
                    emoji = PART_EMOJI.get(part_key.lower(), "")
                    color_int = PART_COLORS.get(part_key.lower(), DEFAULT_COLOR)
                    color = discord.Color(color_int)

                    embed = discord.Embed(
                        title=f"🎉 {member.display_name if member else mention} found a {part_key}!",
                        description=f"You've been awarded **{part_key}** for **{buildable_key}**. Use `/mysnowman` or `/stocking show` to view your assembled snowman.",
                        color=color,
                    )
                    embed.set_footer(text=f"Congratulations {member.display_name if member else mention}!")

                    try:
                        part_img = ASSETS_DIR / f"buildables/{buildable_key}/parts/{part_key}.png"
                        if not part_img.exists():
                            part_img = ASSETS_DIR / f"stickers/{part_key}.png"
                        if part_img.exists():
                            attached = discord.File(part_img, filename=part_img.name)
                            embed.set_thumbnail(url=f"attachment://{part_img.name}")
                            await message.channel.send(content=f"{emoji} {mention}", embed=embed, file=attached)
                        else:
                            await message.channel.send(content=f"{emoji} {mention}", embed=embed)
                    except Exception:
                        try:
                            await message.channel.send(content=f"{emoji} {mention}", embed=embed)
                        except Exception:
                            pass
                except Exception:
                    logger.exception("rumble_listener: individual award handling failed for user %s", wid)
                    continue

    def get_config_snapshot(self) -> Dict[str, Any]:
        return {
            "rumble_bot_ids": self.rumble_bot_ids,
            "channel_part_map": {str(k): [v[0], v[1]] for k, v in self.channel_part_map.items()},
        }


async def setup(bot: commands.Bot):
    await bot.add_cog(RumbleListenerCog(bot))