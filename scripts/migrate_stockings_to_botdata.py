#!/usr/bin/env python3
"""
Migration script: migrate stockings data to bot.data model

This script:
1. Creates timestamped backups of data/collected_pieces.json and data/stockings.json
2. Merges data/buildables.json into bot_data['buildables'] when present
3. Moves per-user parts from data/stockings.json into bot_data['user_pieces'][uid][buildable]
4. Persists using utils.db_utils.save_data if available, otherwise writes data/collected_pieces.json

Usage:
  python3 scripts/migrate_stockings_to_botdata.py
"""
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)8s] %(name)20s : %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# Paths
ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
STOCKINGS_FILE = DATA_DIR / "stockings.json"
BUILDABLES_FILE = DATA_DIR / "buildables.json"
COLLECTED_PIECES_FILE = DATA_DIR / "collected_pieces.json"
BACKUPS_DIR = DATA_DIR.parent / "backups"
BACKUPS_DIR.mkdir(parents=True, exist_ok=True)

# Import save_data if available
try:
    import sys
    sys.path.insert(0, str(ROOT))
    from utils.db_utils import save_data as db_save_data
    logger.info("Imported save_data from utils.db_utils")
except Exception as e:
    logger.warning("Could not import save_data from utils.db_utils: %s", e)
    db_save_data = None


def create_timestamped_backup(file_path: Path) -> None:
    """Create a timestamped backup of a file if it exists"""
    if not file_path.exists():
        logger.info("Skipping backup for %s (does not exist)", file_path.name)
        return
    
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_name = f"{file_path.stem}_{timestamp}{file_path.suffix}"
    backup_path = BACKUPS_DIR / backup_name
    
    try:
        shutil.copy2(file_path, backup_path)
        logger.info("Created backup: %s", backup_path)
    except Exception as e:
        logger.exception("Failed to create backup of %s: %s", file_path, e)
        raise


def load_json_file(file_path: Path) -> Dict[str, Any]:
    """Load JSON file, return empty dict if not found"""
    if not file_path.exists():
        logger.info("File %s does not exist, returning empty dict", file_path.name)
        return {}
    
    try:
        with file_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
            logger.info("Loaded %s: %d top-level keys", file_path.name, len(data) if isinstance(data, dict) else 0)
            return data or {}
    except Exception as e:
        logger.exception("Failed to load %s: %s", file_path, e)
        return {}


def save_bot_data(bot_data: Dict[str, Any]) -> None:
    """Save bot_data using db_utils.save_data or fallback to collected_pieces.json"""
    try:
        if db_save_data is not None:
            db_save_data(bot_data)
            logger.info("Saved bot_data using db_utils.save_data")
        else:
            # Fallback: write to collected_pieces.json
            with COLLECTED_PIECES_FILE.open("w", encoding="utf-8") as fh:
                json.dump(bot_data, fh, ensure_ascii=False, indent=2)
            logger.info("Saved bot_data to %s (fallback)", COLLECTED_PIECES_FILE)
    except Exception as e:
        logger.exception("Failed to save bot_data: %s", e)
        raise


def migrate() -> None:
    """Main migration function"""
    logger.info("=" * 60)
    logger.info("Starting migration: stockings data to bot.data model")
    logger.info("=" * 60)
    
    # Step 1: Create backups
    logger.info("Step 1: Creating timestamped backups...")
    create_timestamped_backup(COLLECTED_PIECES_FILE)
    create_timestamped_backup(STOCKINGS_FILE)
    create_timestamped_backup(BUILDABLES_FILE)
    
    # Step 2: Load existing data
    logger.info("Step 2: Loading existing data files...")
    bot_data = load_json_file(COLLECTED_PIECES_FILE)
    stockings_data = load_json_file(STOCKINGS_FILE)
    buildables_data = load_json_file(BUILDABLES_FILE)
    
    # Step 3: Merge buildables.json into bot_data['buildables']
    logger.info("Step 3: Merging buildables.json into bot_data['buildables']...")
    if buildables_data:
        bot_data.setdefault("buildables", {})
        for buildable_key, buildable_def in buildables_data.items():
            if buildable_key not in bot_data["buildables"]:
                bot_data["buildables"][buildable_key] = buildable_def
                logger.info("  Added buildable '%s' to bot_data['buildables']", buildable_key)
            else:
                logger.info("  Buildable '%s' already exists in bot_data['buildables'], skipping", buildable_key)
    else:
        logger.info("  No buildables data to merge")
    
    # Step 4: Move per-user parts from stockings.json to bot_data['user_pieces']
    logger.info("Step 4: Moving per-user parts from stockings.json to bot_data['user_pieces']...")
    bot_data.setdefault("user_pieces", {})
    
    users_migrated = 0
    parts_migrated = 0
    
    for uid_str, user_rec in stockings_data.items():
        # Skip non-user entries (if any)
        try:
            int(uid_str)
        except ValueError:
            logger.debug("  Skipping non-user entry: %s", uid_str)
            continue
        
        buildables_rec = user_rec.get("buildables", {}) or {}
        if not buildables_rec:
            continue
        
        bot_data["user_pieces"].setdefault(uid_str, {})
        
        for buildable_key, buildable_rec in buildables_rec.items():
            parts = buildable_rec.get("parts", []) or []
            if not parts:
                continue
            
            # Normalize parts to lowercase unique list
            normalized = []
            seen = set()
            for p in parts:
                pl = str(p).lower()
                if pl not in seen:
                    seen.add(pl)
                    normalized.append(pl)
            
            # Check if user already has parts in bot_data
            existing_parts = bot_data["user_pieces"][uid_str].get(buildable_key, []) or []
            if existing_parts:
                logger.info("  User %s already has %d parts for %s in bot_data, merging...", 
                           uid_str, len(existing_parts), buildable_key)
                # Merge: combine and normalize
                merged = list(existing_parts) + normalized
                final_normalized = []
                final_seen = set()
                for p in merged:
                    pl = str(p).lower()
                    if pl not in final_seen:
                        final_seen.add(pl)
                        final_normalized.append(pl)
                bot_data["user_pieces"][uid_str][buildable_key] = final_normalized
                parts_migrated += len(final_normalized)
                logger.info("    Merged to %d total parts for %s/%s", 
                           len(final_normalized), uid_str, buildable_key)
            else:
                bot_data["user_pieces"][uid_str][buildable_key] = normalized
                parts_migrated += len(normalized)
                logger.info("  Migrated %d parts for user %s / buildable %s", 
                           len(normalized), uid_str, buildable_key)
        
        users_migrated += 1
    
    logger.info("  Migration summary: %d users, %d total parts", users_migrated, parts_migrated)
    
    # Step 5: Persist bot_data
    logger.info("Step 5: Persisting bot_data...")
    save_bot_data(bot_data)
    
    logger.info("=" * 60)
    logger.info("Migration completed successfully!")
    logger.info("=" * 60)
    logger.info("Summary:")
    logger.info("  - Backups created in: %s", BACKUPS_DIR)
    logger.info("  - Users migrated: %d", users_migrated)
    logger.info("  - Total parts migrated: %d", parts_migrated)
    logger.info("  - Buildables in bot_data: %d", len(bot_data.get("buildables", {})))
    logger.info("")
    logger.info("Next steps:")
    logger.info("  1. Restart the bot")
    logger.info("  2. Test /mysnowman and /rumble_builds_leaderboard")
    logger.info("  3. Use !dbg_show_parts to verify data")
    logger.info("=" * 60)


if __name__ == "__main__":
    try:
        migrate()
    except Exception as e:
        logger.exception("Migration failed: %s", e)
        exit(1)
