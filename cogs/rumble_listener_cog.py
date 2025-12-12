#!/usr/bin/env python3
"""
RumbleListenerCog — full replacement (refactored).

Behavior highlights:
- Robust winner extraction (explicit mentions, <@id> tokens, embed WINNER fields, Participants blocks).
- Async name->member resolution with safe fetch fallback for small guilds and a fuzzy-match fallback.
- Announces with a small ping line outside the embed (so the embed itself is plaintext and non-pinging).
- Embed omits footer and part thumbnail and does not suggest `/stocking show`.
- Sanitizes candidate strings to avoid accidental mention tokens inside embed text.
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

WINNER_TITLE_RE = re.compile(r"\bWINNER\b|WON!?", re.IGNORECASE)
ADDITIONAL_WIN_RE = re.compile(r"\b(found (?:a|an)|received|was awarded|winner|won)\b", re.IGNORECASE)
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
        ln_clean = re.sub(r"^[^\w@#]+", "", ln_clean).strip()
        if ln_clean:
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

        Improvements:
        - Prefer names that appear immediately after a WINNER header (either in content or in an embed field/description).
        - Extract names from Participants: blocks in embeds or content.
        - Handle numbered/ranking lists and common "Runners-up" sections.
        - Sanitize candidate lines to remove mention tokens (<@123...>), leading emojis/bullets, and extra punctuation.
        - Deduplicate while preserving order and limit result size.
        """

        def _sanitize_candidate(raw: str) -> str:
            if not raw:
                return ""
            s = raw.strip()
            # Remove explicit mention tokens like <@12345>
            s = re.sub(r"<@!?\d+>", "", s)
            # Remove common leading non-word characters (bullets, emoji, punctuation)
            s = re.sub(r"^[^\w@#]+", "", s, flags=re.UNICODE)
            # Trim surrounding punctuation and special chars
            s = s.strip(" \t\n\r:–—-•·▪|,")
            # Collapse multiple spaces
            s = re.sub(r"\s+", " ", s)
            return s.strip()

        candidates: List[str] = []
        content = message.content or ""

        # 1) Content: look for explicit WINNER lines and take the next non-empty line
        lines = content.splitlines()
        for i, ln in enumerate(lines):
            ln = ln.strip()
            if not ln:
                continue
            if WINNER_TITLE_RE.search(ln):
                # next non-empty line likely holds the winner name
                for j in range(i + 1, min(i + 5, len(lines))):
                    cand = lines[j].strip()
                    if cand:
                        c = _sanitize_candidate(cand)
                        if c:
                            candidates.append(c)
                            break
            # pattern like "WINNER: <name>" on same line
            m = re.search(r"WINNER[:!\-\s]*([^\n\r]{1,80})", ln, re.IGNORECASE)
            if m:
                cand = m.group(1).strip()
                c = _sanitize_candidate(cand)
                if c:
                    candidates.append(c)

        # 2) Embed scanning: title, description, and fields
        try:
            for emb in message.embeds:
                emb_text = " ".join(filter(None, [emb.title or "", emb.description or ""] + [f.value for f in (emb.fields or [])]))

                # a) fields explicitly labelled WINNER or containing WINNER art
                for f in (emb.fields or []):
                    fname = (f.name or "").strip()
                    fval = (f.value or "").strip()
                    if WINNER_TITLE_RE.search(fname) or WINNER_TITLE_RE.search(fval):
                        for line in fval.splitlines():
                            line = line.strip()
                            if line:
                                c = _sanitize_candidate(line)
                                if c:
                                    candidates.append(c)
                                    break

                # b) title/description patterns "WINNER" followed by name in description
                if emb.title and WINNER_TITLE_RE.search(emb.title):
                    if emb.description:
                        for line in emb.description.splitlines():
                            line = line.strip()
                            if line:
                                c = _sanitize_candidate(line)
                                if c:
                                    candidates.append(c)
                                    break

                # c) participants block inside embed text
                if "participants" in emb_text.lower():
                    part_cands = _extract_participants_block(emb_text)
                    for p in part_cands:
                        sp = _sanitize_candidate(p)
                        if sp:
                            candidates.append(sp)
        except Exception:
            logger.exception("rumble_listener: embed parsing failed")

        # 3) Participants block in raw content (if present)
        for p in _extract_participants_block(content):
            sp = _sanitize_candidate(p)
            if sp:
                candidates.append(sp)

        # 4) Capture runners-up / numbered lists (e.g., "1. name", "2. name")
        try:
            for ln in content.splitlines():
                ln = ln.strip()
                if re.match(r"^\s*\d+\.\s+", ln) or re.match(r"^[\u2022\-\*]\s*", ln):
                    ln_clean = re.sub(r"^(?:\d+\.\s*|[\u2022\-\*]\s*)", "", ln)
                    ln_clean = _sanitize_candidate(ln_clean)
                    if ln_clean:
                        candidates.append(ln_clean)
        except Exception:
            pass

        # 5) Dedupe while preserving order and cap the results
        seen = set()
        out: List[str] = []
        for c in candidates:
            cc = (c or "").strip()
            if not cc:
                continue
            if cc not in seen:
                seen.add(cc)
                out.append(cc)
            if len(out) >= 50:
                break

        return out

    async def _match_names_to_member_ids(self, guild: discord.Guild, candidates: List[str]) -> List[int]:
        """
        Async name->member resolution. Uses cached members primarily and may fetch members for small guilds.
        Adds a difflib fallback to tolerate minor formatting differences.
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

        async def _try_match(norm_map_local: Dict[int, Tuple[str, str]], candidates_local: List[str]) -> List[int]:
            out: List[int] = []
            for cand in candidates_local:
                nc = _normalize_name(cand)
                if not nc:
                    continue
                found = None
                # exact normalized equality
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
                # containment / substring match
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
                # prefix token fallback
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

        matched_ids = await _try_match(norm_map, candidates)

        # If nothing matched and guild is small-ish, attempt to fetch members
        if not matched_ids:
            try:
                if guild.member_count and guild.member_count <= 1000:
                    logger.info("rumble_listener: fetching members for guild %s to resolve names (count=%s)", guild.id, guild.member_count)
                    fetched = []
                    async for m in guild.fetch_members(limit=None):
                        fetched.append(m)
                    norm_map = {m.id: (_normalize_name(m.name or ""), _normalize_name(m.display_name or "")) for m in fetched}
                    matched_ids = await _try_match(norm_map, candidates)
            except Exception:
                logger.exception("rumble_listener: failed to fetch members for name resolution")

        # Fuzzy-match (difflib) fallback if still nothing
        if not matched_ids:
            try:
                import difflib
                name_to_id: Dict[str, int] = {}
                for mid, (nname, dname) in norm_map.items():
                    if nname:
                        name_to_id.setdefault(nname, mid)
                    if dname:
                        name_to_id.setdefault(dname, mid)
                pool = list(name_to_id.keys())
                for cand in candidates:
                    nc = _normalize_name(cand)
                    if not nc:
                        continue
                    best = difflib.get_close_matches(nc, pool, n=1, cutoff=0.6)
                    if best:
                        bid = name_to_id.get(best[0])
                        if bid and bid not in matched_ids:
                            matched_ids.append(bid)
            except Exception:
                logger.exception("rumble_listener: fuzzy matching failed")

        # dedupe preserve order
        seen = set()
        out_final = []
        for mid in matched_ids:
            if mid not in seen:
                seen.add(mid)
                out_final.append(mid)
        logger.debug("rumble_listener: matching result for candidates=%r -> %r", candidates, out_final)
        return out_final

    async def _extract_winner_ids(self, message: discord.Message) -> List[int]:
        """
        Async extraction that:
        - returns explicit mentions first,
        - searches for <@id> tokens in message/embeds,
        - searches nearby messages (before and after) for mentions,
        - prioritizes names from embed WINNER fields,
        - resolves candidates to IDs via _match_names_to_member_ids (with fuzzy fallback).
        """
        # 1) explicit mentions on the message (best)
        try:
            if message.mentions:
                ids = [m.id for m in message.mentions]
                logger.debug("rumble_listener: explicit mentions on message -> %r", ids)
                return ids
        except Exception:
            pass

        # 2) explicit <@id> patterns in the message content or embed text
        ids: List[int] = []
        try:
            ids.extend(self._extract_ids_from_text(message.content or ""))
            for emb in message.embeds:
                emb_text = " ".join(filter(None, [emb.title or "", emb.description or ""] + [f.value for f in (emb.fields or [])]))
                ids.extend(self._extract_ids_from_text(emb_text))
        except Exception:
            logger.exception("rumble_listener: id extraction failed")
        ids = [int(x) for x in dict.fromkeys(ids)]
        if ids:
            logger.debug("rumble_listener: found explicit id tokens -> %r", ids)
            return ids

        # 3) look nearby: recent messages before and after for mention tokens
        try:
            async for prev in message.channel.history(limit=8, before=message.created_at, oldest_first=False):
                if prev.mentions:
                    ids = [m.id for m in prev.mentions]
                    logger.debug("rumble_listener: found mentions in previous message -> %r", ids)
                    return ids
                mid_tokens = re.findall(r"<@!?(?P<id>\d+)>", prev.content or "")
                if mid_tokens:
                    ids = [int(mid_tokens[0])]
                    logger.debug("rumble_listener: found id token in previous message -> %r", ids)
                    return ids
            async for after in message.channel.history(limit=6, after=message.created_at, oldest_first=True):
                if after.mentions:
                    ids = [m.id for m in after.mentions]
                    logger.debug("rumble_listener: found mentions in next message -> %r", ids)
                    return ids
                mid_tokens = re.findall(r"<@!?(?P<id>\d+)>", after.content or "")
                if mid_tokens:
                    ids = [int(mid_tokens[0])]
                    logger.debug("rumble_listener: found id token in next message -> %r", ids)
                    return ids
        except Exception:
            logger.exception("rumble_listener: nearby history scan failed")

        # 4) No explicit ids found — extract candidate names from embed WINNER fields first
        winner_candidates: List[str] = []
        try:
            for emb in message.embeds:
                for f in (emb.fields or []):
                    if WINNER_TITLE_RE.search(f.name or "") or WINNER_TITLE_RE.search(f.value or ""):
                        for line in (f.value or "").splitlines():
                            line = line.strip()
                            if line:
                                winner_candidates.append(line)
                                break
                if emb.title and WINNER_TITLE_RE.search(emb.title) and emb.description:
                    for line in emb.description.splitlines():
                        line = line.strip()
                        if line:
                            winner_candidates.append(line)
                            break
        except Exception:
            logger.exception("rumble_listener: embed winner-field extraction failed")

        # If none found in winner fields, fall back to broader extractor
        if not winner_candidates:
            winner_candidates = self._collect_candidate_names(message)

        # dedupe preserving order
        seen = set()
        candidates = []
        for c in winner_candidates:
            c = (c or "").strip()
            if not c:
                continue
            if c not in seen:
                seen.add(c)
                candidates.append(c)
        logger.debug("rumble_listener: candidates (priority WINNER fields first) = %r", candidates)

        if not candidates:
            return []

        # 5) resolve candidate names to member IDs
        try:
            guild = message.guild
            if guild is None:
                return []
            matched = await self._match_names_to_member_ids(guild, candidates)
            logger.debug("rumble_listener: matched ids for candidates=%r -> %r", candidates, matched)
            return matched
        except Exception:
            logger.exception("rumble_listener: name->id matching failed")
            return []

    # listener
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None:
            return

        # DEBUG: brief incoming message summary (use getattr so we don't rely on author id existing)
        try:
            if logger.isEnabledFor(logging.INFO):
                logger.info(
                    "rumble:on_message author=%r author_id=%r channel=%r guild=%r embeds=%d",
                    getattr(message.author, "name", None),
                    getattr(message.author, "id", None),
                    getattr(message.channel, "id", None),
                    getattr(message.guild, "id", None),
                    len(message.embeds or []),
                )
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("rumble:on_message content=%r", message.content)
                for i, emb in enumerate(message.embeds or []):
                    try:
                        logger.debug("rumble:embed[%d] title=%r", i, emb.title)
                        logger.debug("rumble:embed[%d] description=%r", i, emb.description)
                        for j, f in enumerate(emb.fields or []):
                            logger.debug("rumble:embed[%d].field[%d] name=%r value=%r", i, j, f.name, f.value)
                    except Exception:
                        logger.exception("rumble:on_message: failed to inspect embed %d", i)
        except Exception:
            logger.exception("rumble:on_message: debug logging failed")

        # safe author id extraction (some messages may not have an id attribute)
        try:
            author_id = int(getattr(message.author, "id", 0))
        except Exception:
            return

        # If rumble_bot_ids configured, only accept messages from them or messages immediately preceded by them
        if self.rumble_bot_ids and author_id not in self.rumble_bot_ids:
            found_recent_rumble = False
            try:
                async for prev in message.channel.history(limit=6, before=message.created_at, oldest_first=False):
                    try:
                        prev_author_id = getattr(prev.author, "id", None)
                        if prev_author_id is None:
                            continue
                        if int(prev_author_id) in self.rumble_bot_ids:
                            found_recent_rumble = True
                            break
                    except Exception:
                        continue
            except Exception:
                found_recent_rumble = False

            if not found_recent_rumble:
                return

        # Detect a winner-style message (broad heuristics)
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
            # Collect candidate names (plain text) from this message and resolve them to member IDs
            candidates = self._collect_candidate_names(message)  # list[str]
            id_map: Dict[int, str] = {}

            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("rumble:candidates_extracted=%r", candidates)

            try:
                # Resolve candidate names to member IDs (tries cache, may fetch if allowed)
                matched_ids: List[int] = []
                if message.guild is not None and candidates:
                    matched_ids = await self._match_names_to_member_ids(message.guild, candidates)

                # Pair matched ids to candidates in order (best-effort)
                for i, mid in enumerate(matched_ids):
                    try:
                        if i < len(candidates):
                            id_map[int(mid)] = candidates[i]
                    except Exception:
                        continue

                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug("rumble:id_map=%r matched_ids=%r", id_map, matched_ids)
            except Exception:
                logger.exception("rumble_listener: candidate->id mapping failed")

            # Now award and announce for each resolved winner id
            for wid in winner_ids:
                try:
                    awarded = False
                    # Persist the award (StockingCog handles saving/rendering). Do not let it announce (announce=False).
                    if hasattr(stocking_cog, "award_part"):
                        awarded = await getattr(stocking_cog, "award_part")(int(wid), buildable_key, part_key, None, announce=False)
                    elif hasattr(stocking_cog, "award_sticker"):
                        awarded = await getattr(stocking_cog, "award_sticker")(int(wid), part_key, None, announce=False)
                    if not awarded:
                        continue

                    # Get member object if available
                    member = message.guild.get_member(int(wid)) if message.guild else None

                    # Choose display text: prefer the candidate plain-text name we extracted (id_map),
                    # otherwise fall back to the member.name, then a generic fallback.
                    try:
                        display_text = id_map.get(int(wid))
                        if not display_text and member:
                            display_text = member.name
                        if not display_text:
                            display_text = candidates[0] if candidates else f"User {wid}"
                    except Exception:
                        display_text = f"User {wid}"

                    # Sanitize display_text to ensure embed text will not contain mention tokens
                    try:
                        def _mention_repl(m):
                            try:
                                mid = int(m.group(1))
                                if message.guild:
                                    mobj = message.guild.get_member(mid)
                                    if mobj:
                                        return mobj.name
                                return f"User {mid}"
                            except Exception:
                                return ""
                        display_text = re.sub(r"<@!?(\d+)>", _mention_repl, str(display_text))
                    except Exception:
                        display_text = re.sub(r"<@!?\d+>", "User", str(display_text))

                    emoji = PART_EMOJI.get(part_key.lower(), "")
                    color_int = PART_COLORS.get(part_key.lower(), DEFAULT_COLOR)
                    color = discord.Color(color_int)

                    # Build embed (NO footer, NO part thumbnail; and do NOT suggest /stocking show)
                    embed = discord.Embed(
                        title=f"🎉 {display_text} found a {part_key}!",
                        description=f"You've been awarded **{part_key}** for **{buildable_key}**.",
                        color=color,
                    )

                    # Announce: small ping line outside the embed (so embed remains plaintext)
                    try:
                        if member:
                            small_ping = f"-# {member.mention}"
                        else:
                            small_ping = f"-# {display_text}"
                        await message.channel.send(content=small_ping, embed=embed)
                    except Exception:
                        try:
                            await message.channel.send(embed=embed)
                        except Exception:
                            logger.exception("rumble_listener: failed to send award announcement for %s", display_text)
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