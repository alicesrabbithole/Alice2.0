import json
import logging
from pathlib import Path
from typing import Dict, Any

import config

DATA_FILE = Path(__file__).parent.parent / "data" / "collected_pieces.json"
logger = logging.getLogger(__name__)

# --- Data Loading and Saving ---
def load_data() -> Dict[str, Any]:
    """Loads the main data file (collected_pieces.json)."""
    if DATA_FILE.exists():
        with open(DATA_FILE, "r") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                logger.exception("Failed to decode collected_pieces.json. Returning empty dictionary.")
                return {}
    return {}


def save_data(data: Dict[str, Any]):
    """Saves the provided dictionary to the data file."""
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f, indent=4)
    except IOError:
        logger.exception("Failed to save data to collected_pieces.json.")


def backup_data():
    """Creates a backup of the current data file."""
    if DATA_FILE.exists():
        backup_file = DATA_FILE.with_suffix(".json.bak")
        try:
            DATA_FILE.rename(backup_file)
            logger.info(f"Created backup: {backup_file}")
        except IOError:
            logger.exception("Failed to create data backup.")

def get_user_pieces(bot_data, user_id, puzzle_key):
    """
    Returns a list of the collected piece IDs for a given user and puzzle key.
    If none are found, returns an empty list.
    """
    user_id_str = str(user_id)  # in case we get an int
    return bot_data.get("user_pieces", {}).get(user_id_str, {}).get(puzzle_key, [])


# --- File System Syncing ---
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
        image_path = puzzle_dir / "puzzle_image.png"
        grid_size = [3, 3]  # Default

        meta_file = puzzle_dir / "meta.json"
        if meta_file.exists():
            try:
                with open(meta_file, 'r') as f:
                    meta = json.load(f)
                display_name = meta.get("display_name", display_name)

                # Support either grid_size or explicit rows/cols, normalize for downstream use
                grid_size = meta.get("grid_size", grid_size)
                rows = meta.get("rows", grid_size[0] if isinstance(grid_size, list) else 3)
                cols = meta.get("cols", grid_size[1] if isinstance(grid_size, list) else 3)
            except Exception:
                logger.exception(f"Failed reading meta.json for {puzzle_key}; using defaults.")
                rows, cols = grid_size

        # Store image_path RELATIVE TO PUZZLES_ROOT so it includes the slug (e.g. "alice_test/puzzle_image.png")
        puzzles_data[puzzle_key] = {
            "display_name": display_name,
            "image_path": str(image_path.relative_to(puzzle_root)).replace('\\', '/'),
            "rows": rows if isinstance(rows, int) else grid_size[0],
            "cols": cols if isinstance(cols, int) else grid_size[1],
        }

        # Collect piece paths RELATIVE TO PUZZLES_ROOT so they include the slug (e.g. "alice_test/pieces/p7.png")
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
                    # allow non-numeric IDs if present
                    pass

                rel_path = str(piece_file.relative_to(puzzle_root)).replace('\\', '/')
                puzzle_pieces[piece_id] = rel_path

            pieces_data[puzzle_key] = puzzle_pieces

    # Preserve existing user collections and drop channel settings
    current_data["puzzles"] = puzzles_data
    current_data["pieces"] = pieces_data

    logger.info("Sync from file system complete.")
    return current_data


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
    user_pieces = bot_data.setdefault("user_pieces", {})
    user_collection = user_pieces.setdefault(user_id_str, {})
    user_puzzle_pieces = user_collection.setdefault(puzzle_key, [])

    if piece_id not in user_puzzle_pieces:
        user_puzzle_pieces.append(piece_id)
        return True
    return False

def remove_piece_from_user(bot_data: Dict[str, Any], user_id: int, puzzle_key: str, piece_id: str) -> bool:
    """Removes a puzzle piece from a user. Returns True if removed."""
    user_pieces = bot_data.get("user_pieces", {})
    user_collection = user_pieces.get(str(user_id), {})
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

