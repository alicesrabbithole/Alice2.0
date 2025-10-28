import os
import json

def merge_and_generate_config(base_path="puzzles", existing_path="config.json", output_path="config.json"):
    # Load existing config if it exists
    if os.path.exists(existing_path):
        with open(existing_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    else:
        config = {
            "default_puzzle": None,
            "puzzles": {},
            "pieces": {},
            "user_pieces": {},
            "drop_channels": {},
            "staff": []
        }

    existing_puzzles = set(config["puzzles"].keys())

    for folder in os.listdir(base_path):
        puzzle_id = folder
        puzzle_path = os.path.join(base_path, folder)
        if not os.path.isdir(puzzle_path):
            continue

        # Merge missing fields if puzzle already exists
        if puzzle_id in existing_puzzles:
            existing = config["puzzles"][puzzle_id]
            if "base_image" not in existing:
                existing["base_image"] = base_image.replace("\\", "/")
            if "full_image" not in existing:
                existing["full_image"] = full_image.replace("\\", "/")
            if "thumbnail" not in existing:
                existing["thumbnail"] = thumbnail.replace("\\", "/")
            if "grid" not in existing:
                existing["grid"] = [rows, cols]
            if "enabled" not in existing:
                existing["enabled"] = True
            # Still update pieces if missing
            if puzzle_id not in config["pieces"]:
                config["pieces"][puzzle_id] = piece_map
            continue

        # Detect images
        full_image = os.path.join(puzzle_path, "full.png")
        base_image = os.path.join(puzzle_path, "base.png")
        thumbnail = os.path.join(puzzle_path, "thumbnail.png")

        # Detect pieces
        pieces_path = os.path.join(puzzle_path, "pieces")
        piece_map = {}
        if os.path.isdir(pieces_path):
            for filename in os.listdir(pieces_path):
                if filename.endswith(".png"):
                    pid = filename.split("_")[-1].split(".")[0]
                    piece_map[pid] = os.path.join(pieces_path, filename).replace("\\", "/")

        rows, cols = 4, 4
        if len(piece_map) in [9, 16, 25, 36]:
            side = int(len(piece_map) ** 0.5)
            rows, cols = side, side

        config["puzzles"][puzzle_id] = {
            "display_name": puzzle_id.replace("_", " ").title(),
            "full_image": full_image.replace("\\", "/"),
            "base_image": base_image.replace("\\", "/"),
            "thumbnail": thumbnail.replace("\\", "/"),
            "grid": [rows, cols],
            "enabled": True
        }

        config["pieces"][puzzle_id] = piece_map

    # Set default puzzle if missing
    if not config["default_puzzle"] and config["puzzles"]:
        config["default_puzzle"] = next(iter(config["puzzles"]))

    # Save merged config
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)

    print(f"✅ Merged config saved to {output_path}")

# Run it
if __name__ == "__main__":
    merge_and_generate_config()
