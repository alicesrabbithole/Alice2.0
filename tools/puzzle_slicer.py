"""
A command-line tool to slice puzzle images and prepare the necessary
directory structure and metadata for the Alice puzzle bot.
"""
# command - python slicer.py "My Awesome Puzzle" "path/to/my_puzzle_color.png" "path/to/my_puzzle_base.png" --grid 5x5
import json
from PIL import Image
import os
import shutil
import argparse
from pathlib import Path
import re


def slugify(name: str) -> str:
    """Converts a string into a standardized 'slug' format (e.g., 'My Puzzle' -> 'my_puzzle')."""
    name = name.lower().replace(" ", "_")
    name = re.sub(r"[^\w_]", "", name)
    return re.sub(r"_+", "_", name).strip("_")


def slice_puzzle(image_path: Path, output_dir: Path, rows: int, cols: int):
    """Slices a single image into a grid of pieces."""
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(image_path).convert("RGBA") as img:
            width, height = img.size
            piece_width, piece_height = width // cols, height // rows

            if piece_width == 0 or piece_height == 0:
                print("❌ Error: Image dimensions are too small for the specified grid.")
                return

            count = 1
            for r in range(rows):
                for c in range(cols):
                    left, upper = c * piece_width, r * piece_height
                    right, lower = left + piece_width, upper + piece_height
                    piece = img.crop((left, upper, right, lower))
                    piece.save(output_dir / f"{count}.png")
                    count += 1
        print(f"✅ Successfully sliced {image_path.name} into {rows * cols} pieces.")
    except Exception as e:
        print(f"❌ An error occurred during slicing: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Slice a source image and set up puzzle files for the bot.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("display_name", type=str, help="The display name of the puzzle (e.g., 'Cheshire Cat').")
    parser.add_argument("full_image", type=Path, help="Path to the FULL COLOR source image to be sliced.")
    parser.add_argument("base_image", type=Path, help="Path to the TRANSPARENT BASE image for the background.")
    parser.add_argument("--grid", type=str, default="4x4", help="Grid dimensions (e.g., '4x4'). Defaults to 4x4.")
    parser.add_argument("--puzzles_dir", type=Path, default=Path("puzzles"), help="The root directory for puzzles.")

    args = parser.parse_args()

    # --- Pre-flight Checks ---
    if not args.full_image.exists():
        print(f"❌ Error: Full image not found at '{args.full_image}'")
        return
    if not args.base_image.exists():
        print(f"❌ Error: Base image not found at '{args.base_image}'")
        return

    try:
        rows, cols = map(int, args.grid.split('x'))
    except ValueError:
        print("❌ Invalid grid format. Use 'RowsxCols' (e.g., '4x4').")
        return

    # --- Directory and Path Setup ---
    puzzle_slug = slugify(args.display_name)
    puzzle_root = args.puzzles_dir / puzzle_slug
    pieces_dir = puzzle_root / "pieces"

    print(f"\nSetting up puzzle '{args.display_name}' with slug '{puzzle_slug}'...")
    puzzle_root.mkdir(parents=True, exist_ok=True)

    # --- Metadata Generation ---
    meta_path = puzzle_root / "meta.json"
    metadata = {
        "display_name": args.display_name,
        "rows": rows,
        "cols": cols
    }
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, indent=4)
    print(f"📝 Created metadata file with grid dimensions at '{meta_path}'")

    # --- Image Copying and Slicing ---
    dest_full_image = puzzle_root / f"{puzzle_slug}_full.png"
    dest_base_image = puzzle_root / f"{puzzle_slug}_base.png"

    shutil.copy2(args.full_image, dest_full_image)
    print(f"📁 Copied full image to '{dest_full_image}'")

    shutil.copy2(args.base_image, dest_base_image)
    print(f"📁 Copied base image to '{dest_base_image}'")

    slice_puzzle(dest_full_image, pieces_dir, rows, cols)

    print("\n🎉 Done! Run the `/syncpuzzles` command in Discord to load the new puzzle.")
    print("   Make sure the 'puzzles' directory is uploaded to your bot's environment.")


if __name__ == "__main__":
    main()