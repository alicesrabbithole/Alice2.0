#!/usr/bin/env python3
import json, pathlib, sys
ROOT = pathlib.Path.cwd()
STOCK = ROOT / "data" / "stockings.json"
BUILD = ROOT / "data" / "buildables.json"
buildable = "snowman"  # change if needed

def load_json(p):
    if not p.exists():
        print("Missing", p)
        return {}
    return json.load(open(p, "r", encoding="utf-8")) or {}

s = load_json(STOCK)
b = load_json(BUILD)
parts_def = (b.get(buildable, {}) or {}).get("parts", {}) or {}
defined_keys = list(parts_def.keys())

# fallback emoji map (same as cog)
DEFAULT = {
    "carrot": "🥕","hat":"🎩","scarf":"🧣","eyes":"👀","mouth":"👄","buttons":"⚪","arms":"🦴",
}
print(f"Defined parts for '{buildable}': {defined_keys}\n")

for uid, rec in sorted((s or {}).items(), key=lambda x: int(x[0]) if x[0].isdigit() else x[0]):
    brec = ((rec.get("buildables") or {}).get(buildable) or {})
    parts = (brec.get("parts") or []) if brec else []
    parts_norm = [str(p).lower() for p in parts]
    missing = [p for p in defined_keys if p.lower() not in parts_norm]
    # map missing to emoji
    emojis = []
    for p in missing:
        em = None
        try:
            # PART_EMOJI is runtime in cog; we can't import here — prefer buildables' emoji if present
            # fallback to DEFAULT mapping
            em = DEFAULT.get(p.lower())
        except Exception:
            em = DEFAULT.get(p.lower())
        emojis.append(em if em else p)
    print(f"uid={uid:>20}  parts={parts}  missing={missing}  missing_emoji={' '.join(emojis)}")