# inspect_writer.py
import traceback
from cogs import db_utils
from cogs.db_utils import load_data
from PIL import Image

print("module file:", db_utils.__file__)
print("db_utils.write_preview object:", db_utils.write_preview)
print("callable?:", callable(db_utils.write_preview))

data = load_data()
puzzle_key = next(iter(data["puzzles"].keys()))

img = Image.new("RGBA", (64,64), (255,0,0,255))

try:
    out = db_utils.write_preview(puzzle_key, img, "testuser")
    print("write_preview returned:", repr(out))
except Exception:
    print("write_preview raised an exception:")
    traceback.print_exc()
