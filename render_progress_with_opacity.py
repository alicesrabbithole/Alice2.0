
from pathlib import Path
import json
import config
from ui.overlay import render_progress_image   # adjust if overlay.py is not in ui/

PUZZLES_ROOT = Path(r"C:\Users\brian\Desktop\Alice2.0\puzzles")
config.PUZZLES_ROOT = PUZZLES_ROOT

puzzle_slug = "lost_in_a_book"
puzdir = PUZZLES_ROOT / puzzle_slug
meta_path = puzdir / "meta.json"
if not meta_path.exists():
    raise SystemExit(f"meta.json not found at {meta_path}")

# load meta.json (so we use the same meta you edited)
meta_raw = json.loads(meta_path.read_text(encoding="utf-8"))
# handle two meta.json styles:
# - either meta.json is the puzzle meta object itself
# - or it's an object keyed by slug: { "lost_in_a_book": { ... } }
if puzzle_slug in meta_raw and isinstance(meta_raw[puzzle_slug], dict):
    meta = meta_raw[puzzle_slug]
else:
    meta = meta_raw

# Build pieces map (relative paths under puzzles root)
pieces_dir = puzdir / "pieces"
pieces_map = {}
if pieces_dir.exists():
    for p in sorted(pieces_dir.iterdir()):
        if p.suffix.lower() in (".png", ".jpg", ".jpeg"):
            key = p.stem
            pieces_map[key] = str(p.relative_to(PUZZLES_ROOT)).replace("\\","/")

bot_data = {"puzzles": {puzzle_slug: meta}, "pieces": {puzzle_slug: pieces_map}}

# choose sample counts (0, 25%, 50%, 75%, 100%)
total = len(pieces_map) or (int(meta.get("rows",1)) * int(meta.get("cols",1)))
samples = sorted(list({0, total//4, total//2, (3*total)//4, total}))

for cnt in samples:
    keys = sorted(pieces_map.keys(), key=lambda s: int(s.lstrip("pP")) if s.lstrip("pP").isdigit() else s)
    collected = keys[:cnt]
    img_bytes = render_progress_image(bot_data, puzzle_slug, collected)
    out_path = puzdir / f"progress_{cnt:03d}.png"
    out_path.write_bytes(img_bytes)
    print("Wrote", out_path)
print("Done.")