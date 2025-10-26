from PIL import Image
import os
import json
import argparse

def slice_puzzle(
    image_path: str,
    output_folder: str,
    resized_to: tuple[int, int],
    rows: int,
    cols: int,
    puzzle_key: str,
    display_name: str
):
    os.makedirs(output_folder, exist_ok=True)

    # Load and resize base image
    base = Image.open(image_path).convert("RGBA")
    base = base.resize(resized_to, Image.Resampling.LANCZOS)
    width, height = base.size
    piece_width = width // cols
    piece_height = height // rows

    piece_map = {}

    for idx in range(1, rows * cols + 1):
        row = (idx - 1) // cols
        col = (idx - 1) % cols
        x = col * piece_width
        y = row * piece_height

        piece = base.crop((x, y, x + piece_width, y + piece_height))
        piece_path = os.path.join(output_folder, f"p1_{idx}.png")

        # Overwrite protection
        if os.path.exists(piece_path):
            print(f"⚠️ Overwriting existing piece: {piece_path}")

        piece.save(piece_path)
        piece_map[str(idx)] = piece_path.replace("\\", "/")

    # Build puzzle config snippet
    puzzle_config = {
        puzzle_key: {
            "display_name": display_name,
            "full_image": image_path.replace("\\", "/"),
            "rows": rows,
            "cols": cols,
            "enabled": True
        }
    }

    # Build pieces config snippet
    pieces_config = {
        puzzle_key: piece_map
    }

    # Output results to console
    print("\n✅ Puzzle sliced successfully.")
    print("\n📦 JSON snippet for puzzles:")
    print(json.dumps(puzzle_config, indent=2))
    print("\n🧩 JSON snippet for pieces:")
    print(json.dumps(pieces_config, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Slice a puzzle image into grid pieces.")
    parser.add_argument("--key", required=True, help="Puzzle key (example: my_puzzle_key)")
    parser.add_argument("--name", required=True, help="Display name (example: My Puzzle)")
    parser.add_argument("--image", required=True, help="Path to full image")
    parser.add_argument("--size", default="1024x1024", help="Resize to WxH (default: 1024x1024)")
    parser.add_argument("--grid", default="4x4", help="Grid size RowsxCols (default: 4x4)")

    args = parser.parse_args()
    width, height = map(int, args.size.split("x"))
    rows, cols = map(int, args.grid.split("x"))

    slice_puzzle(
        image_path=args.image,
        output_folder=f"puzzles/{args.key}/pieces",
        resized_to=(width, height),
        rows=rows,
        cols=cols,
        puzzle_key=args.key,
        display_name=args.name
    )
    # Example original slicer bash: python tools/puzzle_slicer.py --key alice_test --image puzzles/puzzle1/full_puzzle1.png --output pieces/puzzle1 --rows 4 --cols 4 --transparent puzzles/puzzle1/transparent_puzzle1.png --json data/collected_pieces.json
    # Example slicer redo bash (went from 3x3 to 4x4): generate_preview("pieces/puzzle1", 4, 4, "puzzles/puzzle1/preview.png")