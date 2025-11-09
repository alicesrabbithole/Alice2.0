import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional

import config

DATA_FILE = Path(__file__).parent.parent / "data" / "collected_pieces.json"
logger = logging.getLogger(__name__)

# ===============================
# 1. Data Loading & Saving
# ===============================
def load_data() -> Dict[str, Any]:
    """Loads the main data file (collected_pieces.json)."""
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        except Exception:
            logger.exception("Failed to load collected_pieces.json. Returning empty dictionary.")
    return {}

def save_data(data: Dict[str, Any]) -> None:
    """Saves the provided dictionary to the data file."""
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=4)
    except Exception:
        logger.exception("Failed to save data to collected_pieces.json.")

def backup_data() -> None:
    """Creates a backup of the current data file."""
    if DATA_FILE.exists():
        backup_file = DATA_FILE.with_suffix(".json.bak")
        try:
            DATA_FILE.rename(backup_file)
            logger.info(f"Created backup: {backup_file}")
        except Exception:
            logger.exception("Failed to create data backup.")

# ===============================
# 2. User Pieces Utilities
# ===============================
def get_user_pieces(bot_data: Dict[str, Any], user_id: int, puzzle_key: str) -> list:
    """
    Returns a list of the collected piece IDs for a given user and puzzle key.
    If none are found, returns an empty list.
    """
    user_id_str = str(user_id)
    return bot_data.get("user_pieces", {}).get(user_id_str, {}).get(puzzle_key, [])

def add_piece_to_user(bot_data: Dict[str, Any], user_id: int, puzzle_key: str, piece_id: str) -> bool:
    """Adds a puzzle piece to a user's collection. Returns True if added, False if already owned."""
    user_id_str = str(user_id)
    user_pieces = bot_data.setdefault("user_pieces", {})
    user_collection = user_pieces.setdefault(user_id_str, {})
    puzzle_pieces = user_collection.setdefault(puzzle_key, [])
    if piece_id not in puzzle_pieces:
        puzzle_pieces.append(piece_id)
        return True
    return False

def remove_piece_from_user(bot_data: Dict[str, Any], user_id: int, puzzle_key: str, piece_id: str) -> bool:
    """Removes a puzzle piece from a user. Returns True if removed."""
    user_collection = bot_data.get("user_pieces", {}).get(str(user_id), {})
    if puzzle_key in user_collection and piece_id in user_collection[puzzle_key]:
        user_collection[puzzle_key].remove(piece_id)
        if not user_collection[puzzle_key]:
            del user_collection[puzzle_key]
        return True
    return False

def get_user_collection(bot_data: Dict[str, Any], user_id: int) -> Dict[str, Any]:
    """Retrieves the puzzle collection for a specific user."""
    return bot_data.get("user_pieces", {}).get(str(user_id), {})

def wipe_puzzle_from_all(bot_data: Dict[str, Any], puzzle_key: str) -> int:
    """Removes all collected pieces for a specific puzzle from all users. Returns count of affected users."""
    wiped_count = 0
    user_pieces = bot_data.get("user_pieces", {})
    for user_id in list(user_pieces.keys()):
        if puzzle_key in user_pieces[user_id]:
            del user_pieces[user_id][puzzle_key]
            wiped_count += 1
            if not user_pieces[user_id]:
                del user_pieces[user_id]
    return wiped_count

# ===============================
# 3. Puzzle & Piece Management
# ===============================
def resolve_puzzle_key(bot_data: Dict[str, Any], puzzle_input: str) -> Optional[str]:
    """Finds a puzzle's key from either its key or display name (case-insensitive)."""
    puzzles = bot_data.get("puzzles", {})
    if puzzle_input in puzzles:
        return puzzle_input
    for key, meta in puzzles.items():
        if meta.get("display_name", "").lower() == puzzle_input.lower():
            return key
    return None

def get_puzzle_display_name(bot_data: Dict[str, Any], puzzle_key: str) -> str:
    """Gets the display name for a puzzle, falling back to a formatted key."""
    if not puzzle_key:
        return "Unknown Puzzle"
    puzzle_meta = bot_data.get("puzzles", {}).get(puzzle_key, {})
    return puzzle_meta.get("display_name", puzzle_key.replace("_", " ").title())

# ===============================
# 4. Filesystem Sync: Piece and Puzzle Indexing
# ===============================
def sync_from_fs(current_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Scans the puzzle directory and generates a fresh puzzle/piece structure,
    preserving existing collections and drop channel settings.
    """
    logger.info("Scanning puzzle directory and rebuilding data from file system...")
    puzzles_data: Dict[str, Any] = {}
    pieces_data: Dict[str, Dict[str, str]] = {}

    puzzle_root = config.PUZZLES_ROOT  # e.g., Path("puzzles")
    if not puzzle_root.is_dir():
        logger.error(f"Puzzle root directory not found: {puzzle_root}")
        return current_data

    for puzzle_dir in puzzle_root.iterdir():
        if not puzzle_dir.is_dir():
            continue

        puzzle_key = puzzle_dir.name
        display_name = puzzle_key.replace("_", " ").title()
        meta_file = puzzle_dir / "meta.json"
        # Default grid
        grid_size = [3, 3]
        rows, cols = 3, 3
        if meta_file.exists():
            try:
                with open(meta_file, 'r') as f:
                    meta = json.load(f)
                display_name = meta.get("display_name", display_name)
                grid_size = meta.get("grid_size", grid_size)
                rows = meta.get("rows", grid_size[0] if isinstance(grid_size, list) else 3)
                cols = meta.get("cols", grid_size[1] if isinstance(grid_size, list) else 3)
            except Exception:
                logger.exception(f"Failed reading meta.json for {puzzle_key}; using defaults.")
                rows, cols = grid_size

        # Store image_path RELATIVE TO PUZZLES_ROOT
        image_path = puzzle_dir / "puzzle_image.png"
        puzzles_data[puzzle_key] = {
            "display_name": display_name,
            "image_path": str(image_path.relative_to(puzzle_root)).replace('\\', '/'),
            "rows": rows if isinstance(rows, int) else grid_size[0],
            "cols": cols if isinstance(cols, int) else grid_size[1],
        }

        # Collect piece paths RELATIVE TO PUZZLES_ROOT
        pieces_dir = puzzle_dir / "pieces"
        if pieces_dir.is_dir():
            puzzle_pieces: Dict[str, str] = {}
            for piece_file in sorted(pieces_dir.glob("*.png")):
                stem = piece_file.stem
                # normalize: "p12" -> "12", "12" -> "12"
                if stem.startswith("p") and stem[1:].isdigit():
                    piece_id = stem[1:]
                else:
                    piece_id = stem
                # enforce numeric string IDs (so "01" becomes "1")
                try:
                    piece_id = str(int(piece_id))
                except ValueError:
                    pass
                rel_path = str(piece_file.relative_to(puzzle_root)).replace('\\', '/')
                puzzle_pieces[piece_id] = rel_path
            pieces_data[puzzle_key] = puzzle_pieces

    # Update in-memory data structure
    current_data["puzzles"] = puzzles_data
    current_data["pieces"] = pieces_data
    logger.info("Sync from file system complete.")
    return current_data