# tools/add_puzzle.py
import json
import shutil
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = PROJECT_ROOT / "data" / "collected_pieces.json"
BACKUP_FILE = PROJECT_ROOT / "data" / "collected_pieces.json.bak"

def backup_data():
    if DATA_FILE.exists():
        shutil.copy2(DATA_FILE, BACKUP_FILE)
        print(f"Backup created: {BACKUP_FILE}")
    else:
        print(f"No existing data file found at {DATA_FILE}; a new one will be created.")

def load_data():
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    return {}

def save_data(data):
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved data to {DATA_FILE}")

def prompt_values():
    # Reasonable defaults and prompts (you can run non-interactively by editing below)
    key = input("Internal puzzle key (no spaces, e.g. puzzle2): ").strip()
    display = input("Display name (shown in Discord, e.g. Alice 2): ").strip()
    src_full = input("Path to full puzzle image on your machine: ").strip()
    src_pieces_pattern = input("Path pattern for piece images (folder or glob, e.g. C:\\path\\p2_*.png): ").strip()
    return key, display, src_full, src_pieces_pattern

def ensure_paths(key):
    puzzles_dir = PROJECT_ROOT / "puzzles" / key
    pieces_dir = PROJECT_ROOT / "pieces" / key
    puzzles_dir.mkdir(parents=True, exist_ok=True)
    pieces_dir.mkdir(parents=True, exist_ok=True)
    return puzzles_dir, pieces_dir

def copy_full_image(src_full, dest_full_path):
    shutil.copy2(src_full, dest_full_path)
    print(f"Copied full image to {dest_full_path}")

def copy_piece_images(src_pattern, dest_pieces_dir):
    from glob import glob
    matches = glob(src_pattern)
    if not matches:
        print("No piece images matched the pattern. Skipping piece copy.")
        return []
    copied = []
    for src in matches:
        dest = dest_pieces_dir / Path(src).name
        shutil.copy2(src, dest)
        copied.append(dest.name)
    print(f"Copied {len(copied)} piece images to {dest_pieces_dir}")
    return copied

def update_json(data, key, display, full_rel_path, piece_files_rel):
    puzzles = data.setdefault("puzzles", {})
    puzzles.setdefault(key, {})
    puzzles[key].setdefault("display_name", display)
    puzzles[key]["image_path"] = str(full_rel_path).replace("\\", "/")
    # optional: add per-puzzle pieces mapping under top-level pieces structure
    pieces_top = data.setdefault("pieces", {})
    pieces_top.setdefault(key, {})
    for idx, fname in enumerate(sorted(piece_files_rel), start=1):
        pieces_top[key][str(idx)] = str(Path("pieces") / key / fname).replace("\\", "/")
    return data

def main():
    key, display, src_full, src_pieces_pattern = prompt_values()
    puzzles_dir, pieces_dir = ensure_paths(key)
    backup_data()
    data = load_data()

    dest_full = puzzles_dir / f"full_{key}.png"
    copy_full_image(src_full, dest_full)

    copied_piece_names = copy_piece_images(src_pieces_pattern, pieces_dir)

    full_rel = Path("puzzles") / key / dest_full.name
    updated = update_json(data, key, display, full_rel, copied_piece_names)
    save_data(updated)
    print("Done. Restart your bot and run a test drop to verify the display and image.")

if __name__ == "__main__":
    main()
