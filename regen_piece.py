# regen_piece.py
# regenerated p1_16
from PIL import Image
import os

# Edit these to match your layout
puzzle_folder = r"C:\Users\brian\Desktop\pythonProject1\puzzles\Alice Test"
full_image_path = os.path.join(puzzle_folder, "Alice Test_full.png")  # full image
pieces_dir = os.path.join(puzzle_folder, "pieces")
rows, cols = 4, 4
target_idx = 16  # p1_16

os.makedirs(pieces_dir, exist_ok=True)

with Image.open(full_image_path) as im:
    base = im.convert("RGBA")
    # If you usually resize to a specific size before slicing, set resize_to = (W, H) else None
    resize_to = None  # e.g., (1024, 1024) or None
    if resize_to:
        base = base.resize(resize_to, Image.Resampling.LANCZOS)
    width, height = base.size
    piece_w = width // cols
    piece_h = height // rows

    # Crop to exact grid (drop trailing pixels)
    base = base.crop((0, 0, piece_w * cols, piece_h * rows))

    idx = target_idx
    row = (idx - 1) // cols
    col = (idx - 1) % cols
    x = col * piece_w
    y = row * piece_h
    piece = base.crop((x, y, x + piece_w, y + piece_h))

    out_path = os.path.join(pieces_dir, f"p1_{idx}.png")
    piece.save(out_path)
    print(f"Saved {out_path}")
