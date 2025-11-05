import json
import logging
from pathlib import Path
from typing import Dict, Any

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

# --- Puzzle and Piece Utilities ---

def resolve_puzzle_key(bot_data: Dict[str, Any], puzzle_input: str) -> str | None:
    """Finds a puzzle's key from either its key or display name."""
    puzzles = bot_data.get("puzzles", {})
    if puzzle_input in puzzles:
        return puzzle_input  # It's already the key

    for key, meta in puzzles.items():
        if meta.get("display_name", "").lower() == puzzle_input.lower():
            return key
    return None

# --- THIS IS THE FIX ---
# The function now takes bot_data as an argument, making it consistent
# with the rest of the bot's design and preventing file access conflicts.
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