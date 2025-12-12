# SNOWMAN

#!/usr/bin/env python3
"""
Shared theme/constants for the bot (colors / emoji for stocking/rumble).
Put this in your project (e.g. at repository root) and import from it in cogs.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Tuple

DATA_DIR = Path("data")
_BUILDABLES_PATH = DATA_DIR / "buildables.json"

DEFAULT_COLOR: int = 0x2F3136

CANONICAL_EMOJI: Dict[str, str] = {
    "hat": "🎩",
    "scarf": "🧣",
    "carrot": "🥕",
    "eyes": "👀",
    "mouth": "😄",
    "buttons": "⚪",
    "arms": "✋",
}

CANONICAL_COLORS: Dict[str, int] = {
    "hat": 0x001F3B,
    "scarf": 0x8B0000,
    "carrot": 0xFFA500,
    "eyes": 0x9E9E9E,
    "mouth": 0x9E9E9E,
    "buttons": 0x9E9E9E,
    "arms": 0x6B4423,
}


def generate_part_maps_from_buildables() -> Tuple[Dict[str, str], Dict[str, int]]:
    """
    Read data/buildables.json (if present) and return (part_emoji_map, part_color_map).
    Falls back to CANONICAL_* if file missing or malformed.
    """
    parts_keys = set()
    try:
        if _BUILDABLES_PATH.exists():
            data = json.loads(_BUILDABLES_PATH.read_text(encoding="utf-8") or "{}")
            for bdef in (data or {}).values():
                for pk in (bdef.get("parts") or {}).keys():
                    parts_keys.add(pk)
    except Exception:
        parts_keys = set()

    part_emoji: Dict[str, str] = {}
    part_colors: Dict[str, int] = {}

    if parts_keys:
        for pk in sorted(parts_keys):
            key_lower = pk.lower()
            part_emoji[key_lower] = CANONICAL_EMOJI.get(key_lower, "🔸")
            part_colors[key_lower] = CANONICAL_COLORS.get(key_lower, DEFAULT_COLOR)
    else:
        # fallback: expose canonical list
        part_emoji = dict(CANONICAL_EMOJI)
        part_colors = dict(CANONICAL_COLORS)
    return part_emoji, part_colors