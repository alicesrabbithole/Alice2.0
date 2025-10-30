import json
from PIL import Image
import os
import shutil
import argparse
from pathlib import Path
import re


def slugify(name: str) -> str:
    """Converts a string into a standardized 'slug' format."""
    name = name.lower().replace(" ", "_")
    name = re.sub(r"[^\w_]", "", name)
    return re.sub(r"_+", "_", name).strip("_")


def slice_puzzle(image_path: Path, output_dir: Path, rows: int, cols: int):
    """Slices a single image into a grid of pieces."""
    if not image_path.exists():
        print(f"❌ Error: Image not found at {image_path}")
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(image_path).convert("RGBA") as img:
            width, height = img.size
            piece_width, piece_height = width // cols, height // rows
            count = 1
            for r in range(rows):
                for c in range(cols):
                    left, upper = c * piece_width, r * piece_height
                    right, lower = left + piece_width, upper + piece_height
                    piece = img.crop((left, upper, right, lower))
                    piece.save(output_dir / f"p{count}.png")
                    count += 1
        print(f"✅ Successfully sliced {image_path.name} into {rows * cols} pieces.")
    except Exception as e:
        print(f"❌ An error occurred during slicing: {e}")


def main():
    parser = argparse.ArgumentParser(description="Slice a source image and set up puzzle files for the bot.")
    parser.add_argument("display_name", type=str, help="The display name of the puzzle (e.g., 'Alice Test').")
    parser.add_argument("full_image", type=Path, help="Path to the FULL COLOR source image to be sliced.")
    parser.add_argument("base_image", type=Path, help="Path to the TRANSPARENT BASE image for the background.")
    parser.add_argument("--grid", type=str, default="4x4", help="Grid dimensions (e.g., '4x4'). Defaults to 4x4.")
    args = parser.parse_args()

    try:
        rows, cols = map(int, args.grid.split('x'))
    except ValueError:
        print("❌ Invalid grid format. Use 'RowsxCols' (e.g., '4x4').")
        return

    puzzle_slug = slugify(args.display_name)
    puzzle_root = Path("puzzles") / puzzle_slug
    pieces_dir = puzzle_root / "pieces"

    print(f"Setting up puzzle '{args.display_name}' with slug '{puzzle_slug}'...")
    puzzle_root.mkdir(parents=True, exist_ok=True)

    # --- METADATA FIX: Write the display name to a meta.json file ---
    meta_path = puzzle_root / "meta.json"
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump({"display_name": args.display_name}, f, indent=4)
    print(f"📝 Created metadata file at {meta_path}")

    # Copy images
    shutil.copy2(args.full_image, puzzle_root / f"{puzzle_slug}_full.png")
    shutil.copy2(args.base_image, puzzle_root / f"{puzzle_slug}_base.png")

    slice_puzzle(puzzle_root / f"{puzzle_slug}_full.png", pieces_dir, rows, cols)

    print("\n🎉 Done! Run the `/syncpuzzles` command in Discord to load the new puzzle.")


if __name__ == "__main__":
    main()