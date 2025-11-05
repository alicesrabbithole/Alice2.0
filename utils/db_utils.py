import json
import logging
from pathlib import Path
from typing import Dict, Any

import config

DATA_FILE = Path(__file__).parent.parent / "data.json"
logger = logging.getLogger(__name__)


# --- Data Loading and Saving ---

def load_data() -> Dict[str, Any]:
    """Loads the main data file (data.json)."""
    if DATA_FILE.exists():
        with open(DATA_FILE, "r") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                logger.exception("Failed to decode data.json. Returning empty dictionary.")
                return {}
    return {}


def save_data(data: Dict[str, Any]):
    """Saves the provided dictionary to the data file."""
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=4)
    except IOError:
        logger.exception("Failed to save data to data.json.")


# --- File System Syncing ---

# --- THIS IS THE FIX ---
# This function was removed, but admin_cog needs it. It has been restored.
# It scans your puzzle directories and builds a fresh data structure.
def sync_from_fs() -> Dict[str, Any]:
    """
    Scans the puzzle directory and generates a fresh data structure
    containing all puzzle metadata and piece information.
    """
    logger.info("Scanning puzzle directory and rebuilding data from file system...")
    puzzles_data = {}
    pieces_data = {}

    puzzle_root = config.PUZZLES_ROOT
    if not puzzle_root.is_dir():
        logger.error(f"Puzzle root directory not found: {puzzle_root}")
        return {}

    for puzzle_dir in puzzle_root.iterdir():
        if not puzzle_dir.is_dir():
            continue

        puzzle_key = puzzle_dir.name

        # Load puzzle metadata from a potential meta.json
        display_name = puzzle_key.replace("_", " ").title()
        image_path = puzzle_dir / "puzzle_image.png"
        grid_size = [3, 3]  # Default

        meta_file = puzzle_dir / "meta.json"
        if meta_file.exists():
            with open(meta_file, 'r') as f:
                meta = json.load(f)
                display_name = meta.get("display_name", display_name)
                grid_size = meta.get("grid_size", grid_size)

        puzzles_data[puzzle_key] = {
            "display_name": display_name,
            "image_path": str(image_path.relative_to(puzzle_root.parent)).replace('\\', '/'),
            "grid_size": grid_size
        }

        # Scan for pieces
        pieces_dir = puzzle_dir / "pieces"
        if pieces_dir.is_dir():
            puzzle_pieces = {}
            for piece_file in pieces_dir.glob("*.png"):
                piece_id = piece_file.stem
                puzzle_pieces[piece_id] = str(piece_file.relative_to(puzzle_root.parent)).replace('\\', '/')
            pieces_data[puzzle_key] = puzzle_pieces

    bot_data = {
        "puzzles": puzzles_data,
        "pieces": pieces_data,
        "collections": {},
        "drop_channels": {}
    }
    save_data(bot_data)
    logger.info("Sync from file system complete.")
    return bot_data


# --- Puzzle and Piece Utilities ---

def resolve_puzzle_key(bot_data: Dict[str, Any], puzzle_input: str) -> str | None:
    """Finds a puzzle's key from either its key or display name."""
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


def add_piece_to_user(bot_data: Dict[str, Any], user_id: int, puzzle_key: str, piece_id: str) -> bool:
    """Adds a puzzle piece to a user's collection. Returns True if added, False if already owned."""
    user_id_str = str(user_id)
    collections = bot_data.setdefault("collections", {})
    user_collection = collections.setdefault(user_id_str, {})
    user_puzzle_pieces = user_collection.setdefault(puzzle_key, [])

    if piece_id not in user_puzzle_pieces:
        user_puzzle_pieces.append(piece_id)
        return True
    return False


def get_user_collection(bot_data: Dict[str, Any], user_id: int) -> Dict[str, Any]:
    """Retrieves the puzzle collection for a specific user."""
    return bot_data.get("collections", {}).get(str(user_id), {})


# This function was also needed by admin_cog and has been restored.
def wipe_puzzle_from_all(bot_data: Dict[str, Any], puzzle_key: str):
    """Removes all collected pieces for a specific puzzle from all users."""
    for user_id, collection in bot_data.get("collections", {}).items():
        if puzzle_key in collection:
            del collection[puzzle_key]