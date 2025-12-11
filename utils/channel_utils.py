"""
Channel alias utilities with optional hardcoded mappings.

- Edit HARDCODED_ALIASES and HARDCODED_CATEGORY_ALIASES to hard-code alias -> id pairs.
- Set USE_HARDCODED = True to use in-file hardcoded mappings (persistent file ignored).
- Provides resolve_channel(bot, guild, identifier) and resolve_category(bot, guild, identifier).
"""
import json
import re
import unicodedata
from pathlib import Path
from typing import Optional, Dict, Any

import discord

DATA_DIR = Path("data")
ALIASES_FILE = DATA_DIR / "channel_aliases.json"

# Use hardcoded mappings if True (edit HARDCODED_* below)
USE_HARDCODED = True

# --- Edit these mappings for your environment ---
HARDCODED_ALIASES: Dict[str, int] = {
    # "community": 1309962372269609010,
    # "test": 123456789012345678,
    "testing": 1309962373846532159
}

HARDCODED_CATEGORY_ALIASES: Dict[str, int] = {
    # "events": 234567890123456789,
}
# ------------------------------------------------

DATA_DIR.mkdir(parents=True, exist_ok=True)


def _normalize(text: Optional[str]) -> str:
    if text is None:
        return ""
    s = str(text).strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return s.strip()


def load_aliases() -> Dict[str, int]:
    if USE_HARDCODED:
        return { _normalize(k): int(v) for k, v in HARDCODED_ALIASES.items() }
    try:
        if ALIASES_FILE.exists():
            with ALIASES_FILE.open("r", encoding="utf-8") as fh:
                data = json.load(fh) or {}
                return { _normalize(str(k)): int(v) for k, v in data.items() }
    except Exception:
        pass
    return {}


def save_aliases(data: Dict[str, int]) -> None:
    if USE_HARDCODED:
        return
    try:
        ALIASES_FILE.parent.mkdir(parents=True, exist_ok=True)
        with ALIASES_FILE.open("w", encoding="utf-8") as fh:
            json.dump({str(k): int(v) for k, v in data.items()}, fh, ensure_ascii=False, indent=2)
    except Exception:
        pass


def set_alias(alias: str, channel_id: int) -> None:
    if USE_HARDCODED:
        raise RuntimeError("Aliases are hardcoded in utils/channel_utils.py (USE_HARDCODED=True). Edit HARDCODED_ALIASES in this file.")
    norm = _normalize(alias)
    aliases = load_aliases()
    aliases[norm] = int(channel_id)
    save_aliases(aliases)


def remove_alias(alias: str) -> bool:
    if USE_HARDCODED:
        raise RuntimeError("Aliases are hardcoded in utils/channel_utils.py (USE_HARDCODED=True). Edit HARDCODED_ALIASES in this file.")
    norm = _normalize(alias)
    aliases = load_aliases()
    if norm in aliases:
        del aliases[norm]
        save_aliases(aliases)
        return True
    return False


def list_aliases() -> Dict[str, int]:
    return load_aliases()


async def resolve_channel(bot: discord.Client, guild: Optional[discord.Guild], identifier: Optional[Any]) -> Optional[discord.TextChannel]:
    """
    Resolve identifier -> TextChannel. Accepts mention, id, alias, pretty-font name, or real name.
    """
    if not identifier:
        return None
    ident = str(identifier).strip()

    # mention <#id>
    m = re.match(r"<#(\d+)>$", ident)
    if m:
        cid = int(m.group(1))
        ch = bot.get_channel(cid)
        if isinstance(ch, discord.TextChannel):
            return ch

    # numeric id
    if ident.isdigit():
        ch = bot.get_channel(int(ident))
        if isinstance(ch, discord.TextChannel):
            return ch

    # alias lookup
    aliases = load_aliases()
    norm = _normalize(ident)
    if norm in aliases:
        cid = aliases[norm]
        ch = bot.get_channel(int(cid))
        if isinstance(ch, discord.TextChannel):
            return ch
        try:
            if guild:
                ch = guild.get_channel(int(cid))
                if isinstance(ch, discord.TextChannel):
                    return ch
        except Exception:
            pass

    # name/display_name matching in guild
    if guild:
        for ch in guild.text_channels:
            if _normalize(ch.name) == norm or _normalize(getattr(ch, "display_name", "")) == norm:
                return ch
        for ch in guild.text_channels:
            if _normalize(ch.name).startswith(norm) or _normalize(getattr(ch, "display_name", "")).startswith(norm):
                return ch

    # fallback: search all channels in cache
    for ch in bot.get_all_channels():
        if isinstance(ch, discord.TextChannel) and (_normalize(ch.name) == norm or _normalize(getattr(ch, "display_name", "")) == norm):
            return ch

    return None


async def resolve_category(bot: discord.Client, guild: Optional[discord.Guild], identifier: Optional[Any]) -> Optional[discord.CategoryChannel]:
    """
    Resolve identifier -> CategoryChannel. Accepts id, alias (HARDCODED_CATEGORY_ALIASES), or normalized name.
    """
    if not identifier:
        return None
    ident = str(identifier).strip()

    # numeric id
    if ident.isdigit():
        maybe = bot.get_channel(int(ident)) if bot else None
        if isinstance(maybe, discord.CategoryChannel):
            return maybe
        if guild:
            maybe2 = guild.get_channel(int(ident))
            if isinstance(maybe2, discord.CategoryChannel):
                return maybe2

    # hardcoded category alias map (if configured)
    if USE_HARDCODED and HARDCODED_CATEGORY_ALIASES:
        norm = _normalize(ident)
        if norm in { _normalize(k): int(v) for k, v in HARDCODED_CATEGORY_ALIASES.items() }:
            cid = { _normalize(k): int(v) for k, v in HARDCODED_CATEGORY_ALIASES.items() }[norm]
            maybe = bot.get_channel(cid)
            if isinstance(maybe, discord.CategoryChannel):
                return maybe
            if guild:
                maybe2 = guild.get_channel(cid)
                if isinstance(maybe2, discord.CategoryChannel):
                    return maybe2

    # name matching in guild
    if guild:
        norm = _normalize(ident)
        for c in guild.categories:
            if _normalize(c.name) == norm:
                return c
        for c in guild.categories:
            if _normalize(c.name).startswith(norm):
                return c

    return None