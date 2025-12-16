#!/usr/bin/env python3
"""
RumbleListenerCog (strict-author version)

Only processes messages authored by IDs in data/rumble_listener_config.json (rumble_bot_ids)
and only in channels listed in channel_part_map (or global key "0").
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

import discord
from discord.ext import commands

from utils.snowman_theme import DEFAULT_COLOR, CANONICAL_EMOJI, CANONICAL_COLORS, generate_part_maps_from_buildables

logger = logging.getLogger(__name__)

DATA_DIR = Path("data")
CONFIG_FILE = DATA_DIR / "rumble_listener_config.json"
ASSETS_DIR = DATA_DIR / "stocking_assets"

WINNER_TITLE_RE = re.compile(r"\bWINNER\b|WON!?", re.IGNORECASE)
ADDITIONAL_WIN_RE = re.compile(r"\b(found (?:a|an)|received|was awarded|winner|won)\b", re.IGNORECASE)

PART_EMOJI, PART_COLORS = generate_part_maps_from_buildables()

AWARD_DEBOUNCE_SECONDS = 120


def ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _normalize_name(s: str) -> str:
    if not s:
        return ""
    nk = unicodedata.normalize("NFKD", s)
    nk = "".join(ch for ch in nk if not unicodedata.combining(ch))
    nk = nk.lower()
    nk = re.sub(r"[^\w\s]", " ", nk)
    nk = re.sub(r"\s+", " ", nk).strip()
    return nk


def _extract_participants_block(text: str) -> List[str]:
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


def _indefinite_article(word: str) -> str:
    if not word:
        return "a"
    return "an" if word[0].lower() in "aeiou" else "a"


def format_award_phrase_for_listener(buildable_key: str, part_key: str, build_def: Optional[dict] = None) -> str:
    build_def = build_def or {}
    pdef = (build_def.get("parts", {}) or {}).get(part_key, {}) or {}
    display = pdef.get("display_name") or part_key.replace("_", " ")
    is_plural = bool(pdef.get("plural")) or display.endswith("s")
    if is_plural:
        return f"awarded **{display}** for your **{buildable_key}**"
    article = pdef.get("article")
    if not article:
        article = _indefinite_article(display)
    return f"awarded {article} **{display}** for your **{buildable_key}**"


class RumbleListenerCog(commands.Cog):
    def __init__(self, bot: commands.Bot, initial_config: Optional[Dict[str, Any]] = None):
        self.bot = bot
        self._lock = asyncio.Lock()
        ensure_data_dir()
        self.rumble_bot_ids: List[int] = []
        self.channel_part_map: Dict[int, Tuple[str, str]] = {}
        self._recent_awards: Dict[Tuple[int, int, int, str, str], float] = {}
        if initial_config:
            self._load_from_dict(initial_config)
        self._load_config_file()
        logger.info(
            "rumble_listener: loaded rumble_bot_ids=%r channel_part_map=%r",
            self.rumble_bot_ids,
            self.channel_part_map,
        )

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
        def _sanitize_candidate(raw: str) -> str:
            if not raw:
                return ""
            s = raw.strip()
            s = re.sub(r"<@!?\d+>", "", s)
            s = re.sub(r"^[^\w@#]+", "", s, flags=re.UNICODE)
            s = s.strip(" \t\n\r:–—-•·▪|,")
            s = re.sub(r"\s+", " ", s)
            return s.strip()

        candidates: List[str] = []
        content = message.content or ""

        lines = content.splitlines()
        for i, ln in enumerate(lines):
            ln = ln.strip()
            if not ln:
                continue
            if WINNER_TITLE_RE.search(ln):
                for j in range(i + 1, min(i + 5, len(lines))):
                    cand = lines[j].strip()
                    if cand:
                        c = _sanitize_candidate(cand)
                        if c:
                            candidates.append(c)
                            break
            m = re.search(r"WINNER[:!\-\s]*([^\n\r]{1,80})", ln, re.IGNORECASE)
            if m:
                cand = m.group(1).strip()
                c = _sanitize_candidate(cand)
                if c:
                    candidates.append(c)

        try:
            for emb in message.embeds:
                emb_text = " ".join(
                    filter(
                        None,
                        [emb.title or "", emb.description or ""] + [f.value for f in (emb.fields or [])],
                    )
                )

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

                if emb.title and WINNER_TITLE_RE.search(emb.title):
                    if emb.description:
                        for line in emb.description.splitlines():
                            line = line.strip()
                            if line:
                                c = _sanitize_candidate(line)
                                if c:
                                    candidates.append(c)
                                    break

                if "participants" in emb_text.lower():
                    part_cands = _extract_participants_block(emb_text)
                    for p in part_cands:
                        sp = _sanitize_candidate(p)
                        if sp:
                            candidates.append(sp)
        except Exception:
            logger.exception("rumble_listener: embed parsing failed")

        for p in _extract_participants_block(content):
            sp = _sanitize_candidate(p)
            if sp:
                candidates.append(sp)

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

        matched_ids = await _try_match(norm_map, candidates)

        if not matched_ids:
            try:
                if getattr(self.bot, "intents", None) and getattr(self.bot.intents, "members", False):
                    logger.info(
                        "rumble_listener: no matches from cache; attempting fetch-members fallback for guild %s (may be slow).",
                        guild.id,
                    )
                    fetched = []
                    async for m in guild.fetch_members(limit=None):
                        fetched.append(m)
                    norm_map = {m.id: (_normalize_name(m.name or ""), _normalize_name(m.display_name or "")) for m in fetched}
                    matched_ids = await _try_match(norm_map, candidates)
                else:
                    logger.debug("rumble_listener: bot missing members intent; cannot fetch members for better matching.")
            except Exception:
                logger.exception("rumble_listener: fetch-members fallback failed")

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

        seen = set()
        out_final = []
        for mid in matched_ids:
            if mid not in seen:
                seen.add(mid)
                out_final.append(mid)
        logger.debug("rumble_listener: matching result for candidates=%r -> %r", candidates, out_final)
        return out_final

    async def _extract_winner_ids(self, message: discord.Message) -> List[int]:
        try:
            if message.mentions:
                ids = [m.id for m in message.mentions]
                logger.debug("rumble_listener: explicit mentions on message -> %r", ids)
                return ids
        except Exception:
            pass

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

        if not winner_candidates:
            winner_candidates = self._collect_candidate_names(message)

        seen = set()
        candidates = []
        for c in winner_candidates:
            c = (c or "").strip()
            if not c:
                continue
            if c not in seen:
                seen.add(c)
                candidates.append(c)

        if not candidates:
            return []

        try:
            guild = message.guild
            if guild is None:
                return []
            matched = await self._match_names_to_member_ids(guild, candidates)
            return matched
        except Exception:
            logger.exception("rumble_listener: name->id matching failed")
            return []

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None:
            return

        # only process messages in configured channels (or global key 0)
        if self.channel_part_map:
            try:
                ch_id = int(getattr(message.channel, "id", 0))
            except Exception:
                return
            if ch_id not in self.channel_part_map and 0 not in self.channel_part_map:
                return

        # strict author-only: require configured rumble_bot_ids
        try:
            author_id = int(getattr(message.author, "id", 0))
        except Exception:
            return
        if self.rumble_bot_ids and author_id not in self.rumble_bot_ids:
            return

        # debug logging
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

        # detect winner-style message
        found = False
        if message.content and WINNER_TITLE_RE.search(message.content):
            found = True
        if not found and message.content and ADDITIONAL_WIN_RE.search(message.content):
            found = True
        for emb in message.embeds:
            if (emb.title and (WINNER_TITLE_RE.search(emb.title) or ADDITIONAL_WIN_RE.search(emb.title))) or (
                emb.description
                and (WINNER_TITLE_RE.search(emb.description) or ADDITIONAL_WIN_RE.search(emb.description))
            ):
                found = True
                break
            for f in (emb.fields or []):
                if (
                    WINNER_TITLE_RE.search(f.name or "")
                    or WINNER_TITLE_RE.search(f.value or "")
                    or ADDITIONAL_WIN_RE.search(f.name or "")
                    or ADDITIONAL_WIN_RE.search(f.value or "")
                ):
                    found = True
                    break
            if found:
                break
        if not found:
            return

        try:
            winner_ids = await self._extract_winner_ids(message)
        except Exception:
            logger.exception("rumble_listener: winner id extraction failed")
            winner_ids = []

        logger.info("rumble:found_winner_ids=%r channel=%s", winner_ids, message.channel.id)
        logger.info("rumble:channel_mapping_for_channel=%r", self.channel_part_map.get(message.channel.id))

        if not winner_ids:
            try:
                cands = self._collect_candidate_names(message)
                logger.info("rumble:candidates_extracted=%r", cands)
                for i, emb in enumerate(message.embeds or []):
                    try:
                        logger.info("rumble:embed_dump[%d] title=%r description=%r fields=%r", i, emb.title, emb.description, [(f.name, f.value) for f in (emb.fields or [])])
                    except Exception:
                        logger.exception("rumble_listener: failed to dump embed %d for debug", i)
            except Exception:
                logger.exception("rumble_listener: failed to collect candidate names for debug")
            return

        mapping = self.channel_part_map.get(message.channel.id)
        if not mapping:
            mapping = self.channel_part_map.get(0) or self.channel_part_map.get("0")
            if not mapping:
                logger.info("rumble: no mapping for channel=%s; skipping", message.channel.id)
                return

        buildable_key, part_key = mapping
        stocking_cog = self.bot.get_cog("StockingCog")
        if stocking_cog is None:
            logger.info("rumble:stocking_cog_present=False; skipping awards")
            return
        logger.info("rumble:stocking_cog_present=True")

        async with self._lock:
            candidates = self._collect_candidate_names(message)
            id_map: Dict[int, str] = {}
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("rumble:candidates_extracted=%r", candidates)

            try:
                matched_ids: List[int] = []
                if message.guild is not None and candidates:
                    matched_ids = await self._match_names_to_member_ids(message.guild, candidates)

                for i, mid in enumerate(matched_ids):
                    try:
                        if i < len(candidates):
                            id_map[int(mid)] = candidates[i]
                    except Exception:
                        continue
                logger.debug("rumble:id_map=%r matched_ids=%r", id_map, matched_ids)
            except Exception:
                logger.exception("rumble_listener: candidate->id mapping failed")

            for wid in winner_ids:
                try:
                    target_id = int(wid)
                    try:
                        g_id = int(message.guild.id) if message.guild else 0
                        cache_key = (g_id, int(message.channel.id), int(target_id), str(buildable_key), str(part_key))
                        now_ts = time.time()
                        last_ts = self._recent_awards.get(cache_key)
                        if last_ts and (now_ts - last_ts) < AWARD_DEBOUNCE_SECONDS:
                            logger.info("rumble:skipping duplicate award (recent) %s", cache_key)
                            continue
                    except Exception:
                        cache_key = (0, int(message.channel.id), int(target_id), str(buildable_key), str(part_key))
                        now_ts = time.time()

                    logger.info(
                        "rumble:awarding_attempt wid=%s buildable=%s part=%s channel=%s",
                        target_id,
                        buildable_key,
                        part_key,
                        message.channel.id,
                    )

                    member: Optional[discord.Member] = None
                    user_obj: Optional[discord.User] = None
                    try:
                        if message.guild:
                            member = message.guild.get_member(target_id)
                    except Exception:
                        member = None

                    if member is None and message.guild:
                        try:
                            member = await message.guild.fetch_member(target_id)
                        except Exception:
                            member = None

                    if member is None:
                        try:
                            user_obj = self.bot.get_user(target_id) or await self.bot.fetch_user(target_id)
                        except Exception:
                            user_obj = None

                    awarded = False
                    try:
                        if hasattr(stocking_cog, "award_part"):
                            awarded = await getattr(stocking_cog, "award_part")(
                                target_id, buildable_key, part_key, message.channel, announce=False
                            )
                        elif hasattr(stocking_cog, "award_sticker"):
                            awarded = await getattr(stocking_cog, "award_sticker")(target_id, part_key, None, announce=False)
                    except Exception:
                        logger.exception("rumble: award call raised for wid=%s", target_id)
                        awarded = False

                    logger.info("rumble:award_result wid=%s awarded=%s", target_id, bool(awarded))

                    if not awarded:
                        logger.info(
                            "rumble:award skipped for wid=%s (already has part or award failed): buildable=%s part=%s",
                            target_id,
                            buildable_key,
                            part_key,
                        )
                        continue

                    try:
                        self._recent_awards[cache_key] = now_ts
                        if len(self._recent_awards) > 5000:
                            cutoff = now_ts - (AWARD_DEBOUNCE_SECONDS * 2)
                            for k, ts in list(self._recent_awards.items()):
                                if ts < cutoff:
                                    del self._recent_awards[k]
                    except Exception:
                        pass

                    try:
                        if member:
                            display_text = member.display_name or member.name
                        elif user_obj:
                            display_text = getattr(user_obj, "name", f"User {target_id}")
                        else:
                            display_text = id_map.get(int(target_id)) or (candidates[0] if candidates else f"User {target_id}")
                    except Exception:
                        display_text = id_map.get(int(target_id)) or f"User {target_id}"

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

                    try:
                        build_def = None
                        if stocking_cog:
                            build_def = getattr(stocking_cog, "_buildables_def", {}).get(buildable_key)
                        award_phrase = format_award_phrase_for_listener(buildable_key, part_key, build_def)
                    except Exception:
                        logger.exception("rumble_listener: failed to format award phrase")
                        award_phrase = f"received **{part_key}** for your **{buildable_key}**"

                    snowman_emoji = "☃️"
                    emoji = PART_EMOJI.get(part_key.lower(), "")
                    color_int = PART_COLORS.get(part_key.lower(), DEFAULT_COLOR)
                    color = discord.Color(color_int)

                    embed_title = f"{snowman_emoji} Congratulations, {display_text}!"
                    embed = discord.Embed(title=embed_title, description=f"You've been {award_phrase}.", color=color)

                    try:
                        external_mention = member.mention if member else (getattr(user_obj, "mention", None) or f"<@{target_id}>")
                        await message.channel.send(content=f"-# {external_mention}", embed=embed)
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