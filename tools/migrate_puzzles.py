import json
import argparse
from pathlib import Path

# Resolve data.json relative to the project root (one level up from tools/)
SCRIPT_DIR = Path(__file__).resolve().parent
JSON_PATH = SCRIPT_DIR.parent / "data" / "collected_pieces.json"

def load_data():
    with open(JSON_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_data(data):
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)
    print(f"✅ Saved changes to {JSON_PATH}")

def migrate_key(old_key, new_key):
    data = load_data()

    if data.get("default_puzzle") == old_key:
        data["default_puzzle"] = new_key

    if old_key in data.get("puzzles", {}):
        data["puzzles"][new_key] = data["puzzles"].pop(old_key)

    old_map = data.get("pieces", {}).get(old_key)
    if old_map:
        data["pieces"][new_key] = old_map
        del data["pieces"][old_key]
    else:
        print(f"⚠️ No pieces found for '{old_key}', skipping migration.")

    for user, puzzles in data.get("user_pieces", {}).items():
        if old_key in puzzles:
            puzzles[new_key] = puzzles.pop(old_key)

    for ch_id, cfg in data.get("drop_channels", {}).items():
        if cfg.get("puzzle") == old_key:
            cfg["puzzle"] = new_key

    save_data(data)
    print(f"🔄 Migrated {old_key} → {new_key}")

def add_puzzle(key, display_name=None, full_image_path=None, pieces_dir=None):
    data = load_data()

    if key in data.get("puzzles", {}):
        print(f"⚠️ Puzzle '{key}' already exists.")
        return

    # Add puzzle metadata
    data.setdefault("puzzles", {})[key] = {
        "display_name": display_name or key.title(),
        "full_image": full_image_path or f"puzzles/{key}/full.png"
    }

    # Add pieces
    pieces_dict = {}
    if pieces_dir:
        folder = Path(pieces_dir)
        if folder.exists() and folder.is_dir():
            # sort files so numbering is consistent
            files = sorted(folder.glob("*.png"))
            for idx, file in enumerate(files, start=1):
                # store relative path for portability
                pieces_dict[str(idx)] = str(file.as_posix())
            print(f"🧩 Added {len(files)} pieces from {folder}")
        else:
            print(f"⚠️ Pieces folder {folder} not found.")
    data.setdefault("pieces", {})[key] = pieces_dict

    save_data(data)
    print(f"✨ Added new puzzle '{display_name or key.title()}' with skeleton entries.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Puzzle JSON migration helper")
    subparsers = parser.add_subparsers(dest="command")

    # migrate
    mig = subparsers.add_parser("migrate", help="Rename a puzzle key everywhere")
    mig.add_argument("old_key")
    mig.add_argument("new_key")

    # add
    add = subparsers.add_parser("add", help="Add a new puzzle skeleton")
    add.add_argument("key")
    add.add_argument("--display", help="Display name", default=None)
    add.add_argument("--full", help="Full image path", default=None)
    add.add_argument("--pieces", help="Folder containing piece PNGs", default=None)

    args = parser.parse_args()

    if args.command == "migrate":
        migrate_key(args.old_key, args.new_key)
    elif args.command == "add":
        add_puzzle(args.key, args.display, args.full, args.pieces)
    else:
        parser.print_help()

    # powershell for full_puzzle1 to alice example: python C:\Users\brian\Desktop\pythonProject1\tools\migrate_puzzles.py migrate full_puzzle1 alice
