# tools/patch_config.py
import os
import json
from typing import Optional

def _project_root_from_path(path: str) -> str:
    # If a file path was passed (like bot.py path), return its directory
    return os.path.dirname(os.path.abspath(path))

def patch_config(config_path: str, puzzles_dir: Optional[str] = None, project_anchor: Optional[str] = None):
    """
    Patch config.json with image paths discovered under puzzles_dir.

    - config_path: path to config.json to update (absolute or relative)
    - puzzles_dir: optional explicit path to puzzles folder; if omitted the function
      will look for a 'puzzles' folder next to project_anchor or next to config_path.
    - project_anchor: optional path (e.g. path to bot.py) to anchor relative resolution.
    """
    # Resolve config path absolute
    config_path_abs = os.path.abspath(config_path)

    # Determine anchor for resolving puzzles_dir
    if project_anchor:
        anchor_dir = _project_root_from_path(project_anchor)
    else:
        anchor_dir = os.path.dirname(config_path_abs)

    # Default puzzles_dir (prefer explicit arg)
    if puzzles_dir:
        puzzles_dir_abs = os.path.abspath(puzzles_dir)
    else:
        puzzles_dir_abs = os.path.join(anchor_dir, "puzzles")

    # If puzzles dir is still missing, try one level up (common when running from subfolder)
    if not os.path.isdir(puzzles_dir_abs):
        alt = os.path.join(anchor_dir, "..", "puzzles")
        if os.path.isdir(os.path.abspath(alt)):
            puzzles_dir_abs = os.path.abspath(alt)

    # Final check
    if not os.path.isdir(puzzles_dir_abs):
        print(f"⚠️ puzzles directory not found: {puzzles_dir_abs}")
        print("⚠️ Skipping patch. To fix, either:")
        print("  - ensure the puzzles folder exists at that path")
        print("  - or call patch_config(config_path, puzzles_dir='C:/full/path/to/puzzles', project_anchor='path/to/bot.py')")
        return

    # Load config
    if not os.path.exists(config_path_abs):
        print(f"⚠️ Config file not found: {config_path_abs}")
        return

    with open(config_path_abs, "r", encoding="utf-8") as f:
        config = json.load(f)

    patched = False

    for folder in os.listdir(puzzles_dir_abs):
        puzzle_path = os.path.join(puzzles_dir_abs, folder)
        if not os.path.isdir(puzzle_path):
            continue

        puzzle_key = folder.replace(" ", "_").lower()
        puzzle = config.setdefault("puzzles", {}).setdefault(puzzle_key, {})

        base = os.path.join(puzzle_path, f"{folder}_base.png")
        full = os.path.join(puzzle_path, f"{folder}_full.png")
        thumb = os.path.join(puzzle_path, f"{folder}_thumbnail.png")

        def _rel_if_exists(p):
            if os.path.exists(p):
                return os.path.relpath(p, start=os.path.dirname(config_path_abs)).replace("\\", "/")
            return None

        for k, p in (("base_image", base), ("full_image", full), ("thumbnail", thumb)):
            rel = _rel_if_exists(p)
            if rel and puzzle.get(k) != rel:
                puzzle[k] = rel
                patched = True

        # pieces
        pieces_dir = os.path.join(puzzle_path, "pieces")
        if os.path.isdir(pieces_dir):
            pieces_map = config.setdefault("pieces", {}).setdefault(puzzle_key, {})
            for filename in os.listdir(pieces_dir):
                if not filename.lower().endswith(".png"):
                    continue
                try:
                    pid = filename.split("_")[-1].split(".")[0]
                except Exception:
                    continue
                piece_path = os.path.join(pieces_dir, filename)
                rel = os.path.relpath(piece_path, start=os.path.dirname(config_path_abs)).replace("\\", "/")
                if pieces_map.get(pid) != rel:
                    pieces_map[pid] = rel
                    patched = True

        # sensible defaults
        if "display_name" not in puzzle:
            puzzle["display_name"] = folder
            patched = True
        if "enabled" not in puzzle:
            puzzle["enabled"] = True
            patched = True

    if patched:
        with open(config_path_abs, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
        print(f"✅ Patched config saved to {config_path_abs}")
    else:
        print("✅ Config already up to date.")
