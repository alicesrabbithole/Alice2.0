#!/usr/bin/env python3
"""
Migrate data/stockings.json + data/buildables.json into the bot.data model:
 - bot_data["user_pieces"][uid_str][buildable] = parts_list
 - bot_data["buildables"] = buildables_def

This script will:
 - create a timestamped backup of data/collected_pieces.json (if present) and data/stockings.json
 - merge stockings.json into collected_pieces structure
 - write using utils.db_utils.save_data if available; otherwise write to data/collected_pieces.json
"""
from __future__ import annotations
import json, shutil, sys
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path.cwd()
DATA_DIR = ROOT / "data"
STOCK_FILE = DATA_DIR / "stockings.json"
BUILD_FILE = DATA_DIR / "buildables.json"
COLLECTED_FILE = DATA_DIR / "collected_pieces.json"

ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
bak_collected = COLLECTED_FILE.with_name(f"{COLLECTED_FILE.name}.bak.{ts}")
bak_stock = STOCK_FILE.with_name(f"{STOCK_FILE.name}.bak.{ts}")

def load_json(p: Path):
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8") or "{}")
    except Exception as e:
        print("Failed to load", p, e)
        return {}

def save_collected(bot_data):
    # Prefer using utils.db_utils.save_data if available
    try:
        from utils.db_utils import save_data as _save_data
    except Exception:
        _save_data = None
    if _save_data:
        _save_data(bot_data)
        print("Saved via utils.db_utils.save_data()")
    else:
        if not COLLECTED_FILE.parent.exists():
            COLLECTED_FILE.parent.mkdir(parents=True, exist_ok=True)
        with COLLECTED_FILE.open("w", encoding="utf-8") as fh:
            json.dump(bot_data, fh, ensure_ascii=False, indent=2)
        print("Wrote", COLLECTED_FILE)

def main():
    # backup existing collected file if present
    if COLLECTED_FILE.exists():
        shutil.copy2(COLLECTED_FILE, bak_collected)
        print("Backed up:", COLLECTED_FILE, "->", bak_collected)
    # backup stockings
    if STOCK_FILE.exists():
        shutil.copy2(STOCK_FILE, bak_stock)
        print("Backed up:", STOCK_FILE, "->", bak_stock)

    bot_data = load_json(COLLECTED_FILE) or {}
    # ensure top-level containers exist
    bot_data.setdefault("user_pieces", {})
    bot_data.setdefault("buildables", {})

    # import buildables.json if present and bot_data has none
    buildables = load_json(BUILD_FILE)
    if buildables:
        # do not overwrite existing entries unless empty
        for k, v in buildables.items():
            if k not in bot_data["buildables"]:
                bot_data["buildables"][k] = v
        print("Merged buildables.json to bot_data['buildables']")

    # import stockings.json -> user_pieces
    stock = load_json(STOCK_FILE)
    if stock:
        count = 0
        for uid_str, rec in stock.items():
            buildables_rec = (rec.get("buildables") or {}) or {}
            if not buildables_rec:
                continue
            up = bot_data.setdefault("user_pieces", {})
            user_map = up.setdefault(str(uid_str), {})
            for bkey, brec in buildables_rec.items():
                parts = (brec.get("parts") or []) or []
                if parts:
                    # prefer existing runtime entries but ensure uniqueness
                    existing = set(user_map.get(bkey, []))
                    merged = list(existing.union({str(p).lower() for p in parts}))
                    user_map[bkey] = merged
                    count += 1
        print(f"Migrated parts for {count} buildable entries from stockings.json into bot_data['user_pieces']")
    else:
        print("No stockings.json to migrate (or it's empty)")

    # persist
    save_collected(bot_data)
    print("Migration complete. Keep backups:", bak_collected if bak_collected.exists() else "(none)", bak_stock if bak_stock.exists() else "(none)")

if __name__ == "__main__":
    main()