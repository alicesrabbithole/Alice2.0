# test_composer_debug.py
from cogs.puzzle_composer import PuzzleComposer
from ui.overlay import build_puzzle_progress
from cogs.db_utils import load_data, write_preview
from PIL import Image

data = load_data()
puzzle_key = next(iter(data["puzzles"].keys()))
user_id = "testuser"

# quick sanity-check writer
img = Image.new("RGBA", (64,64), (255,0,0,255))
print("writer test ->", repr(write_preview(puzzle_key, img, user_id)))

# Composer build
composer = PuzzleComposer(data, collected=None)
res1 = composer.build(puzzle_key, user_id)
print("Composer.build returned ->", repr(res1))

# Overlay builder
res2 = build_puzzle_progress(puzzle_key, [], data, user_id=user_id)
print("build_puzzle_progress returned ->", repr(res2))
