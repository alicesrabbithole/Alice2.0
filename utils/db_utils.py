import os
import json
import re
import shutil
import logging
from typing import Dict, Any, Optional
from datetime import datetime

import config

logger = logging.getLogger(__name__)

DEFAULT_DATA = {"puzzles": {}, "pieces": {}, "user_pieces": {}, "drop_channels": {}, "staff": []}


def load_data() -> Dict[str, Any]:
    """Loads the main data file (collected_pieces.json)."""
    if not config.DB_PATH.exists():
        config.DATA_DIR.mkdir(exist_ok=True)
        save_data(DEFAULT_DATA)
        return DEFAULT_DATA.copy()
    try:
        with open(config.DB_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, TypeError):
        logger.exception("Failed to load database, returning default data.")
        return DEFAULT_DATA.copy()


def save_data(data: Dict[str, Any]):
    """Saves the provided data dictionary to the main data file."""
    config.DATA_DIR.mkdir(exist_ok=True)
    temp_path = config.DB_PATH.with_suffix(".json.tmp")
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    os.replace(temp_path, config.DB_PATH)


def backup_data():
    """Creates a timestamped backup of the main data file."""
    if not config.DB_PATH.exists():
        return
    config.BACKUP_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = config.BACKUP_DIR / f"backup_{config.DB_PATH.stem}_{timestamp}.json"
    try:
        shutil.copy2(config.DB_PATH, backup_path)
        logger.info(f"Data backup saved to {backup_path}")
    except Exception:
        logger.exception("Failed to create data backup.")


def get_puzzle_display_name(data: dict, puzzle_key: str) -> str:
    """Gets the display name for a puzzle."""
    if not puzzle_key:
        return "Unknown Puzzle"
    meta = data.get("puzzles", {}).get(puzzle_key, {})
    return meta.get("display_name", puzzle_key.replace("_", " ").title())


def sync_from_fs() -> Dict[str, Dict]:
    """Syncs puzzle structure and pieces from the filesystem."""
    puzzles, pieces = {}, {}
    if not config.PUZZLES_ROOT.is_dir():
        logger.error(f"Puzzles root directory not found at: {config.PUZZLES_ROOT}")
        return {"puzzles": {}, "pieces": {}}

    for puzzle_dir in config.PUZZLES_ROOT.iterdir():
        if not puzzle_dir.is_dir():
            continue
        slug = puzzle_dir.name

        meta_path = puzzle_dir / "meta.json"
        meta = {}
        if meta_path.exists():
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)

        display_name = meta.get("display_name", slug.replace("_", " ").title())
        rows = meta.get("rows", 4)
        cols = meta.get("cols", 4)

        # --- THE FIX: Use .relative_to() to store only the part of the path *after* PUZZLES_ROOT ---
        def get_relative_path(full_path):
            if full_path:
                return str(full_path.relative_to(config.PUZZLES_ROOT)).replace("\\", "/")
            return None

        full_img = next(puzzle_dir.glob("*_full.png"), None)
        base_img = next(puzzle_dir.glob("*_base.png"), None)

        puzzle_pieces = {}
        pieces_dir = puzzle_dir / "pieces"
        if pieces_dir.is_dir():
            piece_files = sorted(
                pieces_dir.glob("p*.png"),
                key=lambda p: int(re.search(r'p(\d+)', p.name).group(1)) if re.search(r'p(\d+)', p.name) else 0
            )
            for i, piece_path in enumerate(piece_files, start=1):
                # Apply the fix to piece paths as well
                puzzle_pieces[str(i)] = get_relative_path(piece_path)

        if "rows" not in meta and puzzle_pieces:
            num_pieces = len(puzzle_pieces)
            rows = cols = int(num_pieces ** 0.5)
            if rows * cols != num_pieces:
                for i in range(int(num_pieces ** 0.5), 0, -1):
                    if num_pieces % i == 0:
                        rows = i
                        cols = num_pieces // i
                        break

        # Apply the fix to base and full image paths
        puzzles[slug] = {
            "display_name": display_name,
            "full_image": get_relative_path(full_img),
            "base_image": get_relative_path(base_img),
            "rows": rows,
            "cols": cols
        }
        pieces[slug] = puzzle_pieces

    return {"puzzles": puzzles, "pieces": pieces}


def resolve_puzzle_key(data: dict, identifier: str) -> Optional[str]:
    """Finds a puzzle's key (slug) from a user-provided identifier."""
    if not identifier:
        return None
    puzzles = data.get("puzzles", {})
    norm_id = identifier.lower().strip()

    if identifier in puzzles:
        return identifier
    if norm_id in puzzles:
        return norm_id

    for slug, meta in puzzles.items():
        if meta.get("display_name", "").lower() == norm_id:
            return slug
    return None


def add_piece_to_user(data: dict, user_id: int, puzzle_key: str, piece_id: str) -> bool:
    """Adds a puzzle piece to a user's collection. Returns False if they already have it."""
    user_pieces = data.setdefault("user_pieces", {}).setdefault(str(user_id), {})
    piece_list = user_pieces.setdefault(puzzle_key, [])
    if piece_id not in piece_list:
        piece_list.append(piece_id)
        return True
    return False


def remove_piece_from_user(data: dict, user_id: int, puzzle_key: str, piece_id: str) -> bool:
    """Removes a specific puzzle piece from a user's collection."""
    user_puzzle_pieces = data.get("user_pieces", {}).get(str(user_id), {}).get(puzzle_key)
    if user_puzzle_pieces and piece_id in user_puzzle_pieces:
        user_puzzle_pieces.remove(piece_id)
        return True
    return False


def wipe_puzzle_from_all(data: dict, puzzle_key: str) -> int:
    """Removes all pieces for a specific puzzle from all users."""
    wiped_count = 0
    user_pieces = data.get("user_pieces", {})
    for user_id in list(user_pieces.keys()):
        if puzzle_key in user_pieces[user_id]:
            del user_pieces[user_id][puzzle_key]
            wiped_count += 1
    return wiped_count