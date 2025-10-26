# debug_run.py
import os
import json
from pprint import pprint

PROJECT_ROOT = os.getcwd()
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config.json")
COLLECTED_PATH = os.path.join(PROJECT_ROOT, "data", "collected_pieces.json")
PUZZLES_DIR = os.path.join(PROJECT_ROOT, "puzzles")
TARGET_PUZZLE_NAMES = ["Alice Test", "alice_test", "Alice_Test"]  # try variants

def load_json(path):
    if not os.path.exists(path):
        print(f"Missing file: {path}")
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def list_puzzle_files(folder):
    if not os.path.isdir(folder):
        return []
    out = {}
    out["root_files"] = [f for f in os.listdir(folder) if os.path.isfile(os.path.join(folder, f))]
    pieces_dir = os.path.join(folder, "pieces")
    out["pieces_exists"] = os.path.isdir(pieces_dir)
    out["piece_files"] = sorted([f for f in os.listdir(pieces_dir)]) if out["pieces_exists"] else []
    return out

def main():
    print("Project root:", PROJECT_ROOT)
    cfg = load_json(CONFIG_PATH)
    collected = load_json(COLLECTED_PATH)

    print("\n=== config.json puzzles keys ===")
    puzzles_keys = list(cfg.get("puzzles", {}).keys())
    pprint(puzzles_keys)

    print("\n=== bot.collected user_pieces top-level keys (sample) ===")
    pprint(list(collected.get("user_pieces", {}).items())[:5])

    print("\n=== pieces mapping counts in config.json ===")
    pieces_map = cfg.get("pieces", {})
    for k in puzzles_keys:
        print(f"{k}: {len(pieces_map.get(k, {}))} pieces")

    # Show filesystem for likely puzzle folders
    print("\n=== Puzzle folders on disk (listing puzzles/ root) ===")
    if os.path.isdir(PUZZLES_DIR):
        for name in sorted(os.listdir(PUZZLES_DIR)):
            path = os.path.join(PUZZLES_DIR, name)
            if os.path.isdir(path):
                info = list_puzzle_files(path)
                print(f"\nFolder: {name}")
                print("  root files:", info["root_files"])
                print("  pieces exists:", info["pieces_exists"])
                print("  piece files (count):", len(info["piece_files"]))
                if info["piece_files"]:
                    print("  sample pieces:", info["piece_files"][:10])
    else:
        print("puzzles/ folder not found at", PUZZLES_DIR)

    # Try to locate the puzzle key used by viewpuzzle logic
    print("\n=== Attempt to resolve puzzle key for variants ===")
    for candidate in TARGET_PUZZLE_NAMES:
        resolved = None
        # exact
        if candidate in puzzles_keys:
            resolved = candidate
        else:
            # case-insensitive key match
            for key in puzzles_keys:
                if key.lower() == candidate.lower():
                    resolved = key
                    break
            # display-name match
            if not resolved:
                for key, info in cfg.get("puzzles", {}).items():
                    if candidate.lower() == str(info.get("display_name", "")).lower():
                        resolved = key
                        break
        print(f"Candidate: {candidate} -> Resolved key: {resolved}")

if __name__ == "__main__":
    main()

