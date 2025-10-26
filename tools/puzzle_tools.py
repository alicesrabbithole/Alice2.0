# tools/puzzle_tools.py
from PIL import Image
import os
import json
import re
from typing import Tuple, Dict, List

def slugify(name: str, maxlen: int = 32) -> str:
    """
    Make a filesystem-safe, lowercase puzzle key from a display name.
    Example: "Alice Test!" -> "alice_test"
    """
    s = name.lower()
    s = re.sub(r"[^\w\s-]", "", s)                # remove punctuation
    s = re.sub(r"\s+", "_", s).strip("_")         # spaces -> underscores
    return s[:maxlen]

def load_config(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_config(path: str, cfg: dict):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

def validate_and_collect_pieces(pieces_dir: str, rows: int, cols: int) -> Tuple[Dict[str, str], List[str]]:
    """
    Ensure expected piece files exist and are valid images.
    Returns (piece_map, problems)
    piece_map uses string IDs "1".."N" mapped to relative paths.
    """
    problems: List[str] = []
    piece_map: Dict[str, str] = {}
    expected_count = rows * cols

    if not os.path.isdir(pieces_dir):
        problems.append(f"pieces directory not found: {pieces_dir}")
        return {}, problems

    # Ensure expected filenames exist (p1_1..p1_N)
    for idx in range(1, expected_count + 1):
        filename = f"p1_{idx}.png"
        path = os.path.join(pieces_dir, filename)
        if not os.path.exists(path):
            problems.append(f"missing piece: {filename}")
            continue
        # Try opening with PIL
        try:
            with Image.open(path) as im:
                im.verify()
        except Exception as e:
            problems.append(f"unreadable piece {filename}: {e}")
            continue
        piece_map[str(idx)] = os.path.relpath(path).replace("\\", "/")

    return piece_map, problems

def add_puzzle_from_existing(
    config_path: str,
    puzzle_key: str,
    display_name: str,
    full_image_path: str,
    pieces_dir: str,
    rows: int,
    cols: int,
    overwrite: bool = False
) -> Tuple[bool, List[str]]:
    """
    Create or update a puzzle entry in config using an existing pieces folder and full image.
    Returns (changed, messages). messages contains validation/warning strings.
    """
    messages: List[str] = []
    cfg = load_config(config_path)

    puzzles = cfg.setdefault("puzzles", {})
    pieces = cfg.setdefault("pieces", {})

    # Basic presence checks
    if not os.path.exists(full_image_path):
        messages.append(f"full image not found: {full_image_path}")
        return False, messages

    piece_map, problems = validate_and_collect_pieces(pieces_dir, rows, cols)
    messages.extend(problems)
    if not piece_map:
        messages.append("no valid pieces collected; aborting.")
        return False, messages

    # Ensure puzzle key uniqueness unless overwrite
    if puzzle_key in puzzles and not overwrite:
        messages.append(f"puzzle key already exists: {puzzle_key} (use overwrite=True to replace)")
        return False, messages

    # Write puzzle metadata
    puzzles[puzzle_key] = {
        "display_name": display_name,
        "full_image": os.path.relpath(full_image_path).replace("\\", "/"),
        "rows": rows,
        "cols": cols,
        "enabled": True
    }

    # Write pieces map
    pieces[puzzle_key] = piece_map

    # Persist config
    save_config(config_path, cfg)
    messages.append("config updated and saved.")
    return True, messages
