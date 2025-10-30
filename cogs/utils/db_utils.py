import os
import json
import re
import shutil
import logging
from typing import Dict, Any, Optional
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

DATA_DIR = Path("data")
DB_PATH = DATA_DIR / "collected_pieces.json"
PUZZLES_ROOT = Path("puzzles")
DEFAULT_DATA = {"puzzles": {}, "pieces": {}, "user_pieces": {}, "drop_channels": {}, "staff": []}

# --- HARD-CODED PUZZLE NAMES ---
PUZZLE_NAME_MAP = {
    "alice_test": "Alice Test"
}
# ---

def load_data() -> Dict[str, Any]:
    if not DB_PATH.exists():
        DATA_DIR.mkdir(exist_ok=True)
        save_data(DEFAULT_DATA)
        return DEFAULT_DATA.copy()
    try:
        with open(DB_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, TypeError):
        return DEFAULT_DATA.copy()

def save_data(data: Dict[str, Any]):
    DATA_DIR.mkdir(exist_ok=True)
    with open(DB_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def backup_data():
    """Creates a timestamped backup of the main data file."""
    if not DB_PATH.exists(): return
    backup_dir = DATA_DIR / "backups"
    backup_dir.mkdir(exist_ok=True)
    backup_path = backup_dir / f"backup_{DB_PATH.name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    try:
        shutil.copy2(DB_PATH, backup_path)
        logger.info(f"Data backup saved to {backup_path}")
    except Exception:
        logger.exception("Failed to create data backup.")

def get_puzzle_display_name(data: dict, puzzle_key: str) -> str:
    """Gets the display name for a puzzle, prioritizing the hard-coded map."""
    if not puzzle_key: return "Unknown Puzzle"
    if puzzle_key in PUZZLE_NAME_MAP:
        return PUZZLE_NAME_MAP[puzzle_key]
    meta = data.get("puzzles", {}).get(puzzle_key, {})
    return meta.get("display_name", puzzle_key.replace("_", " ").title())

def sync_from_fs(puzzle_root: Path = PUZZLES_ROOT) -> Dict[str, Dict]:
    """Syncs puzzle structure and pieces from the filesystem."""
    puzzles, pieces = {}, {}
    if not puzzle_root.is_dir(): return {"puzzles": {}, "pieces": {}}

    for puzzle_dir in puzzle_root.iterdir():
        if not puzzle_dir.is_dir(): continue
        slug = puzzle_dir.name
        display_name = get_puzzle_display_name({}, slug)
        full_img = next(puzzle_dir.glob("*_full.png"), None)
        base_img = next(puzzle_dir.glob("*_base.png"), None)
        puzzle_pieces = {}
        pieces_dir = puzzle_dir / "pieces"
        if pieces_dir.is_dir():
            piece_files = sorted(pieces_dir.glob("p*.png"), key=lambda p: int(re.search(r'p(\d+)', p.name).group(1)) if re.search(r'p(\d+)', p.name) else 0)
            for i, piece_path in enumerate(piece_files, start=1):
                puzzle_pieces[str(i)] = str(piece_path).replace("\\", "/")
        rows = cols = int(len(puzzle_pieces) ** 0.5) if puzzle_pieces else 4
        puzzles[slug] = {"display_name": display_name, "full_image": str(full_img).replace("\\", "/"), "base_image": str(base_img).replace("\\", "/"), "rows": rows, "cols": cols}
        pieces[slug] = puzzle_pieces
    return {"puzzles": puzzles, "pieces": pieces}

def resolve_puzzle_key(data: dict, identifier: str) -> Optional[str]:
    if not identifier: return None
    puzzles = data.get("puzzles", {})
    norm_id = identifier.lower().strip()
    if identifier in puzzles: return identifier
    if norm_id in puzzles: return norm_id
    for slug, meta in puzzles.items():
        if get_puzzle_display_name(data, slug).lower() == norm_id:
            return slug
    return None

def is_staff(data: dict, user) -> bool: return str(user.id) in data.get("staff", [])

def add_piece_to_user(data: dict, user_id: int, puzzle_key: str, piece_id: str) -> bool:
    piece_list = data.setdefault("user_pieces", {}).setdefault(str(user_id), {}).setdefault(puzzle_key, [])
    if piece_id not in piece_list: piece_list.append(piece_id); return True
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
    for user_id in user_pieces:
        if puzzle_key in user_pieces[user_id]:
            del user_pieces[user_id][puzzle_key]
            wiped_count += 1
    return wiped_count