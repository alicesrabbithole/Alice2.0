# quick_test_preview.py
from cogs.puzzle_composer import PuzzleComposer
from ui.overlay import build_puzzle_progress
from cogs.db_utils import load_data, save_data, DEFAULT_DATA

data = load_data()
print("Loaded data keys:", list(data.keys()))

# Ensure the file has the expected structure; create defaults if totally missing
if "puzzles" not in data:
    print("No 'puzzles' key found in data. Writing DEFAULT_DATA to disk so you can populate puzzles.")
    save_data(DEFAULT_DATA)
    data = load_data()

if not data.get("puzzles"):
    print("No puzzles present. Two options:")
    print("  1) Populate the 'data/collected_pieces.json' with puzzles (via sync_puzzle_images or by hand).")
    print("  2) Place puzzle folders under 'puzzles/' and run your sync function (sync_puzzle_images) from the bot.")
    raise SystemExit("Aborting quick test: no puzzles available to test.")

# Pick the first puzzle and a test user id
puzzle_key = next(iter(data["puzzles"].keys()))
user_id = "1234567890"  # change to a real test user id if you have one

print("Using puzzle_key:", puzzle_key)

composer = PuzzleComposer(data, collected=None)
out1 = composer.build(puzzle_key, user_id)
print("Composer wrote:", out1)

collected = data.get("user_pieces", {}).get(user_id, {}).get(puzzle_key, [])
out2 = build_puzzle_progress(puzzle_key, collected, data, user_id=user_id)
print("Overlay builder wrote:", out2)