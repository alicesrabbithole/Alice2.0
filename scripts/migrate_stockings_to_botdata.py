#!/usr/bin/env python3
"""
Migration script: Migrate data/stockings.json to bot.data model

This script:
1. Backs up data/collected_pieces.json and data/stockings.json with timestamped copies
2. Merges data/buildables.json into bot_data['buildables'] if present
3. Copies per-user buildable parts from data/stockings.json into bot_data['user_pieces'][uid][buildable]
4. Persists using utils.db_utils.save_data(bot_data) if available, otherwise writes data/collected_pieces.json

Usage:
    python3 scripts/migrate_stockings_to_botdata.py [--dry-run]

Options:
    --dry-run    Show what would be migrated without making changes
"""

import sys
import json
import shutil
from pathlib import Path
from datetime import datetime

# Determine repo root
SCRIPT_DIR = Path(__file__).parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"

STOCKINGS_FILE = DATA_DIR / "stockings.json"
BUILDABLES_FILE = DATA_DIR / "buildables.json"
COLLECTED_PIECES_FILE = DATA_DIR / "collected_pieces.json"


def timestamp_str():
    """Returns a timestamp string for backup files."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def backup_file(file_path: Path):
    """Creates a timestamped backup of a file."""
    if not file_path.exists():
        print(f"⏭  No file to back up: {file_path}")
        return None
    
    backup_path = file_path.with_suffix(f".{timestamp_str()}.bak")
    try:
        shutil.copy2(file_path, backup_path)
        print(f"✅ Backed up {file_path.name} → {backup_path.name}")
        return backup_path
    except Exception as e:
        print(f"❌ Failed to back up {file_path}: {e}")
        sys.exit(1)


def load_json(file_path: Path):
    """Load JSON from a file, return empty dict if not found."""
    if not file_path.exists():
        print(f"ℹ️  File not found: {file_path}, using empty dict")
        return {}
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"❌ Failed to load {file_path}: {e}")
        sys.exit(1)


def save_json(file_path: Path, data: dict):
    """Save dict to JSON file with pretty formatting."""
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        print(f"✅ Saved {file_path}")
    except Exception as e:
        print(f"❌ Failed to save {file_path}: {e}")
        sys.exit(1)


def migrate(dry_run=False):
    """Perform the migration."""
    print("=" * 70)
    print("🔄 Stocking Data Migration to bot.data Model")
    print("=" * 70)
    
    if dry_run:
        print("🔍 DRY RUN MODE - No changes will be made\n")
    
    # Step 1: Backup existing files
    print("\n📦 Step 1: Backing up existing files...")
    if not dry_run:
        backup_file(COLLECTED_PIECES_FILE)
        backup_file(STOCKINGS_FILE)
    else:
        print(f"  Would back up: {COLLECTED_PIECES_FILE.name}")
        if STOCKINGS_FILE.exists():
            print(f"  Would back up: {STOCKINGS_FILE.name}")
    
    # Step 2: Load existing data
    print("\n📂 Step 2: Loading existing data...")
    bot_data = load_json(COLLECTED_PIECES_FILE)
    stockings_data = load_json(STOCKINGS_FILE)
    buildables_def = load_json(BUILDABLES_FILE)
    
    print(f"  Loaded {len(stockings_data)} users from stockings.json")
    print(f"  Loaded {len(buildables_def)} buildables from buildables.json")
    print(f"  Existing user_pieces has {len(bot_data.get('user_pieces', {}))} users")
    
    # Step 3: Merge buildables definitions
    print("\n🔧 Step 3: Merging buildables definitions...")
    if buildables_def:
        if 'buildables' not in bot_data:
            bot_data['buildables'] = {}
        
        for buildable_key, buildable_def in buildables_def.items():
            if buildable_key not in bot_data['buildables']:
                bot_data['buildables'][buildable_key] = buildable_def
                print(f"  Added buildable: {buildable_key}")
            else:
                print(f"  Buildable {buildable_key} already exists in bot.data (skipping)")
    else:
        print("  No buildables to merge")
    
    # Step 4: Migrate per-user buildable parts
    print("\n👥 Step 4: Migrating per-user buildable parts...")
    if 'user_pieces' not in bot_data:
        bot_data['user_pieces'] = {}
    
    migrated_users = 0
    migrated_parts = 0
    
    for uid_str, user_record in stockings_data.items():
        if 'buildables' not in user_record:
            continue
        
        # Ensure user exists in bot_data['user_pieces']
        if uid_str not in bot_data['user_pieces']:
            bot_data['user_pieces'][uid_str] = {}
        
        for buildable_key, buildable_record in user_record['buildables'].items():
            parts = buildable_record.get('parts', []) or []
            
            if not parts:
                continue
            
            # Normalize parts to lowercase unique list
            normalized_parts = []
            seen = set()
            for p in parts:
                pl = str(p).lower()
                if pl not in seen:
                    seen.add(pl)
                    normalized_parts.append(pl)
            
            # Merge with existing parts in bot_data
            if buildable_key not in bot_data['user_pieces'][uid_str]:
                bot_data['user_pieces'][uid_str][buildable_key] = []
            
            existing_parts = set(bot_data['user_pieces'][uid_str][buildable_key])
            for part in normalized_parts:
                if part not in existing_parts:
                    bot_data['user_pieces'][uid_str][buildable_key].append(part)
                    migrated_parts += 1
            
            print(f"  User {uid_str}: {buildable_key} has {len(normalized_parts)} parts")
        
        migrated_users += 1
    
    print(f"\n  Migrated {migrated_parts} parts for {migrated_users} users")
    
    # Step 5: Persist changes
    print("\n💾 Step 5: Persisting changes...")
    if dry_run:
        print("  DRY RUN: Would save to", COLLECTED_PIECES_FILE)
        print("\n📊 Sample of what would be saved:")
        sample_users = list(bot_data.get('user_pieces', {}).keys())[:3]
        for uid in sample_users:
            print(f"    User {uid}: {bot_data['user_pieces'][uid]}")
    else:
        # Try to use db_utils.save_data if available
        try:
            sys.path.insert(0, str(REPO_ROOT))
            from utils import db_utils
            db_utils.save_data(bot_data)
            print(f"  ✅ Saved using db_utils.save_data()")
        except ImportError:
            print("  ℹ️  db_utils not available, writing directly to collected_pieces.json")
            save_json(COLLECTED_PIECES_FILE, bot_data)
        except Exception as e:
            print(f"  ⚠️  db_utils.save_data() failed: {e}")
            print("  Falling back to direct write...")
            save_json(COLLECTED_PIECES_FILE, bot_data)
    
    # Summary
    print("\n" + "=" * 70)
    print("✨ Migration Complete!")
    print("=" * 70)
    print(f"  Buildables in bot.data: {len(bot_data.get('buildables', {}))}")
    print(f"  Users with pieces: {len(bot_data.get('user_pieces', {}))}")
    print(f"  Total parts migrated: {migrated_parts}")
    
    if not dry_run:
        print("\n📝 Next steps:")
        print("  1. Restart the bot to load the new data")
        print("  2. Test /mysnowman for users who had parts")
        print("  3. Test /rumble_builds_leaderboard to see the interactive UI")
        print("  4. Check logs with: journalctl -u your-bot-service | grep 'LB RUN'")
    else:
        print("\n  Re-run without --dry-run to apply changes")


def main():
    """Main entry point."""
    dry_run = "--dry-run" in sys.argv or "-n" in sys.argv
    
    try:
        migrate(dry_run=dry_run)
    except KeyboardInterrupt:
        print("\n\n⚠️  Migration cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Migration failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
