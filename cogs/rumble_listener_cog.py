#!/usr/bin/env python3
"""
RumbleListenerCog with improved winner extraction for plain-text/embed winner names.

Improvements:
- Prefer explicit mentions and <@id> references.
- Parse embed fields, description, and content for "WINNER" labels and the following name lines.
- Parse "Participants:" blocks for candidate names.
- Normalize names and match against guild members (exact normalized equality first, then substring).
- Works for the Rumble Royale format you pasted (WINNER in an embed field, participants block, etc.)
"""
from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import discord
from discord.ext import commands

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
_CANONICAL_EMOJI = {
    "hat": "🎩",
    "scarf": "🧣",
    "carrot": "🥕",
    "eyes": "👀",
    "mouth": "😄",
    "buttons": "⚪",
    "arms": "✋",
}
# color choices by semantic part name (hex ints)
_CANONICAL_COLORS = {
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
    # Unicode normalize and remove combining marks
    nk = unicodedata.normalize("NFKD", s)
    nk = "".join(ch for ch in nk if not unicodedata.combining(ch))
    nk = nk.lower()
    # Replace punctuation with space
    nk = re.sub(r"[^\w\s]", " ", nk)
    # Collapse whitespace
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
    # find the Participants: header
    m = re.search(r"participants\s*[:\-]?\s*(?::)?", text, re.IGNORECASE)
    if not m:
        return candidates
    start = m.end()
    # Capture until next blank line followed by capitalized header (heuristic) or until end
    tail = text[start:]
    # split into lines and take the first block of up to 20 lines (or until an empty line)
    lines = tail.splitlines()
    for ln in lines:
        ln = ln.strip()
        if not ln:
            break
        # each participant line often begins with @ or an emoji then a name; strip leading characters
        # take the last token sequence as name (after optional @ or emoji)
        # remove leading "@" if present
        # also remove extra commas/bullets
        ln_clean = ln.lstrip("•-@").strip()
        # sometimes the line may look like "@Name", or "• @Name", or "emoji Name"
        # Remove common leading emojis / markers using regex
        ln_clean = re.sub(r"^[^\w@#@]+", "", ln_clean).strip()
        # if ln_clean contains multiple tokens but includes an @mention like <@...>, keep that literal
        candidates.append(ln_clean)
        if len(candidates) >= 50:
            break
    return candidates


# --- Winner extraction logic ----------------------------------------------
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
            return

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
            return

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
        Scan message.content and embeds for candidate plain-text names:
        - lines after 'WINNER' headers
        - embed fields with 'WINNER' in the name
        - 'Participants:' block names
        - small heuristics for lines with 'Runners-up' or rankings
        """
        candidates: List[str] = []

        # 1) message content lines
        content = message.content or ""
        for ln in content.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            # Winner header followed by name on the next line is common
            if WINNER_TITLE_RE.search(ln):
                # try next line as name
                # find index of ln in lines
                lines = content.splitlines()
                try:
                    idx = lines.index(ln)
                    if idx + 1 < len(lines):
                        cand = lines[idx + 1].strip()
                        if cand:
                            candidates.append(cand)
                except Exception:
                    pass
            # simple "WINNER! name" on same line
            m = re.search(r"WINNER[^\w]*(?P<name>[\w\W]{1,64})", ln, re.IGNORECASE)
            if m:
                name = m.group("name").strip()
                if name:
                    candidates.append(name)

        # 2) embed title/description/fields scanning
        try:
            for emb in message.embeds:
                # full embed text
                emb_text = " ".join(filter(None, [emb.title or "", emb.description or ""] + [f.value for f in (emb.fields or [])]))
                # check fields explicitly for WINNER label
                for f in (emb.fields or []):
                    if WINNER_TITLE_RE.search(f.name or "") or WINNER_TITLE_RE.search(f.value or ""):
                        # if field.value looks like a username or list, splitlines and take first non-empty token
                        for line in (f.value or "").splitlines():
                            line = line.strip()
                            if line:
                                candidates.append(line)
                                break
                # also check embed.title/description for patterns like "WINNER\nname"
                if emb.title and WINNER_TITLE_RE.search(emb.title):
                    # description or first field may contain the name
                    if emb.description:
                        for line in emb.description.splitlines():
                            line = line.strip()
                            if line:
                                candidates.append(line)
                                break
                # attempt to capture participants in embed text too
                # look for 'Participants' substring and capture following lines
                if "participants" in emb_text.lower():
                    pc = _extract_participants_block(emb_text)
                    candidates.extend(pc)
        except Exception:
            pass

        # 3) participants block in message content
        candidates.extend(_extract_participants_block(content))

        # 4) Runners-up or ranking lists can include names; we'll capture nouns after the numbering
        try:
            for ln in content.splitlines():
                ln = ln.strip()
                if re.match(r"^\s*\d+\.", ln) or re.match(r"^[\u2022\-\*]\s*", ln):
                    # take the line minus prefix
                    ln_clean = re.sub(r"^(?:\d+\.\s*|[\u2022\-\*]\s*)", "", ln)
                    if ln_clean:
                        candidates.append(ln_clean.strip())
        except Exception:
            pass

        # final dedupe preserving order, and trim to sane count
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

    def _match_names_to_member_ids(self, guild: discord.Guild, candidates: List[str]) -> List[int]:
        """
        Try to resolve candidate plain-text names to guild member IDs.
        Strategy:
        - For each candidate, normalize and try exact normalized equality against member.name and member.display_name.
        - If no exact matches, attempt substring containment (normalized).
        - Skip bot accounts by default.
        """
        if not guild or not candidates:
            return []

        # Precompute normalized names for members
        members = guild.members
        norm_map: Dict[int, Tuple[str, str]] = {}
        for m in members:
            try:
                norm_map[m.id] = (_normalize_name(m.name or ""), _normalize_name(m.display_name or ""))
            except Exception:
                norm_map[m.id] = ((m.name or "").lower(), (m.display_name or "").lower())

        matched_ids: List[int] = []
        for cand in candidates:
            nc = _normalize_name(cand)
            if not nc:
                continue
            # 1) exact normalized equality
            best = None
            for mid, (nname, dname) in norm_map.items():
                try:
                    if nname and nname == nc:
                        best = mid
                        break
                    if dname and dname == nc:
                        best = mid
                        break
                except Exception:
                    continue
            if best:
                if best not in matched_ids:
                    matched_ids.append(best)
                continue
            # 2) try containment: member name inside candidate or candidate inside member name
            for mid, (nname, dname) in norm_map.items():
                try:
                    if nname and (nc in nname or nname in nc):
                        best = mid
                        break
                    if dname and (nc in dname or dname in nc):
                        best = mid
                        break
                except Exception:
                    continue
            if best:
                if best not in matched_ids:
                    matched_ids.append(best)
                continue
            # 3) last resort: try simple token match (first token)
            tokens = nc.split()
            if tokens:
                t0 = tokens[0]
                for mid, (nname, dname) in norm_map.items():
                    try:
                        if nname.startswith(t0) or dname.startswith(t0):
                            if mid not in matched_ids:
                                matched_ids.append(mid)
                                break
                    except Exception:
                        continue
        return matched_ids

    @staticmethod
    def _extract_winner_ids(message: discord.Message) -> List[int]:
        """
        Robust winner extraction:
        1) explicit mentions -> return IDs
        2) <@id> patterns in text/embeds -> return IDs
        3) field/title "WINNER" in embeds (value contains name) -> extract candidate names
        4) participants block or lines following WINNER -> candidate names
        5) resolve candidate names to guild member IDs via normalization & matching
        """
        # 1) explicit mentions
        try:
            if message.mentions:
                return [m.id for m in message.mentions]
        except Exception:
            pass

        # 2) explicit <@id> patterns in content or embed text
        ids = []
        try:
            ids.extend(RumbleListenerCog._extract_ids_from_text(message.content or ""))
            # also from embeds
            for emb in message.embeds:
                emb_text = " ".join(filter(None, [emb.title or "", emb.description or ""] + [f.value for f in (emb.fields or [])]))
                ids.extend(RumbleListenerCog._extract_ids_from_text(emb_text))
        except Exception:
            pass
        ids = list(dict.fromkeys(ids))
        if ids:
            return ids

        # 3) Collect candidate plain-text names
        candidates = RumbleListenerCog._collect_candidate_names(RumbleListenerCog, message)  # type: ignore[arg-type]
        # If no candidates found return empty
        if not candidates:
            return []

        # 4) Attempt to match candidates to guild members
        try:
            guild = message.guild
            if guild is None:
                return []
            matched = RumbleListenerCog._match_names_to_member_ids(RumbleListenerCog, guild, candidates)  # type: ignore[arg-type]
            return matched
        except Exception:
            return []

    # rest of listener logic remains unchanged (on_message -> uses _extract_winner_ids)
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None:
            return
        try:
            author_id = int(message.author.id)
        except Exception:
            return

        # If rumble_bot_ids configured, accept only messages from them or preceded by them
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

        winner_ids = RumbleListenerCog._extract_winner_ids(message)
        if not winner_ids:
            # nothing to award (can't identify winners)
            return

        mapping = self.channel_part_map.get(message.channel.id)
        if not mapping:
            # no mapping configured for this channel; try global default (id 0) as fallback
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
                    continue

    def get_config_snapshot(self) -> Dict[str, Any]:
        return {
            "rumble_bot_ids": self.rumble_bot_ids,
            "channel_part_map": {str(k): [v[0], v[1]] for k, v in self.channel_part_map.items()},
        }


async def setup(bot: commands.Bot):
    await bot.add_cog(RumbleListenerCog(bot))