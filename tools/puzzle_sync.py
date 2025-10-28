# tools/run_sync.py
import shutil
from cogs import db_utils

from cogs.db_utils import sync_puzzle_data, normalize_all_puzzle_keys

def initialize_puzzle_data(bot):
    puzzles, pieces, _ = sync_puzzle_data(bot)
    bot.data["puzzles"] = puzzles or {}
    bot.data["pieces"] = pieces or {}
    normalize_all_puzzle_keys(bot)
    print(f"✅ Puzzle data initialized with {len(puzzles)} puzzles and {sum(len(p) for p in pieces.values())} pieces.")
