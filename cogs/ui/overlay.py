import logging
from typing import List, Dict
from PIL import Image, ImageDraw, ImageFont
import io

import config

logger = logging.getLogger(__name__)

# --- Font Loading ---
try:
    FONT = ImageFont.truetype(config.FONT_PATH, 24)
    FONT_SMALL = ImageFont.truetype(config.FONT_PATH, 18)
except IOError:
    logger.warning(f"Font not found at '{config.FONT_PATH}'. Falling back to default font. Please add a .ttf font file to your project.")
    FONT = ImageFont.load_default()
    FONT_SMALL = ImageFont.load_default()


def render_progress_image(bot_data: Dict, puzzle_key: str, collected_piece_ids: List[str]) -> bytes:
    """Renders a user's puzzle progress and a progress bar into a single image."""
    puzzle_meta = bot_data.get("puzzles", {}).get(puzzle_key)
    piece_map = bot_data.get("pieces", {}).get(puzzle_key)

    if not puzzle_meta or not piece_map:
        raise FileNotFoundError(f"Metadata or pieces for puzzle '{puzzle_key}' not found.")

    rows, cols = puzzle_meta.get("rows", 4), puzzle_meta.get("cols", 4)
    tile_size = 96
    img_width, img_height = cols * tile_size, rows * tile_size
    bar_height = 30
    padding = 5
    total_height = img_height + bar_height + (padding * 2)

    # Create the main canvas with Discord's background color
    final_img = Image.new("RGBA", (img_width, total_height), (49, 51, 56, 255))
    draw = ImageDraw.Draw(final_img)

    # --- 1. Draw the Puzzle Image ---
    # Start with the base image if it exists, otherwise a dark canvas
    base_image_path = puzzle_meta.get("base_image")
    try:
        # --- THE ONLY CHANGE IS THE LOGGER.ERROR LINE BELOW ---
        full_path_to_check = config.PUZZLES_ROOT.joinpath(base_image_path) if base_image_path else None
        logger.error(f"DIAGNOSTIC: Checking for base image at path: {full_path_to_check}")

        if full_path_to_check and full_path_to_check.exists():
            puzzle_img = Image.open(full_path_to_check).convert("RGBA").resize((img_width, img_height), Image.Resampling.LANCZOS)
        else:
            puzzle_img = Image.new("RGBA", (img_width, img_height), (30, 30, 30, 255))
    except Exception:
        puzzle_img = Image.new("RGBA", (img_width, img_height), (30, 30, 30, 255))
        logger.exception("Failed to load base puzzle image.")

    # Paste collected pieces onto the base image
    for piece_id in collected_piece_ids:
        piece_path = piece_map.get(str(piece_id))
        try:
            if piece_path and config.PUZZLES_ROOT.joinpath(piece_path).exists():
                with Image.open(config.PUZZLES_ROOT.joinpath(piece_path)).convert("RGBA") as piece_img:
                    piece_img = piece_img.resize((tile_size, tile_size), Image.Resampling.LANCZOS)
                    idx = int(piece_id) - 1
                    r, c = divmod(idx, cols)
                    puzzle_img.paste(piece_img, (c * tile_size, r * tile_size), piece_img)
        except Exception:
            logger.exception(f"Failed to process piece {piece_id} for puzzle {puzzle_key}.")

    # If the puzzle is complete, overlay the full image for better quality
    total_pieces = len(piece_map)
    is_complete = len(collected_piece_ids) == total_pieces
    if is_complete:
        full_path = puzzle_meta.get("full_image")
        try:
            if full_path and config.PUZZLES_ROOT.joinpath(full_path).exists():
                full_img = Image.open(config.PUZZLES_ROOT.joinpath(full_path)).convert("RGBA").resize((img_width, img_height), Image.Resampling.LANCZOS)
                puzzle_img = full_img
        except Exception:
            logger.exception("Failed to load full puzzle image on completion.")

    # Paste the final puzzle grid onto the main canvas
    final_img.paste(puzzle_img, (0, 0))

    # --- 2. Draw the Progress Bar ---
    bar_y = img_height + padding
    ratio = len(collected_piece_ids) / total_pieces if total_pieces > 0 else 0

    # Bar background
    draw.rectangle([padding, bar_y, img_width - padding, bar_y + bar_height], fill=(20, 20, 20, 200), outline=(200, 200, 200, 150))
    # Bar fill (Discord blurple)
    if ratio > 0:
        draw.rectangle([padding, bar_y, padding + (img_width - (padding * 2)) * ratio, bar_y + bar_height], fill=(88, 101, 242, 220))

    # Progress text
    progress_text = f"{len(collected_piece_ids)} / {total_pieces}"
    text_bbox = draw.textbbox((0, 0), progress_text, font=FONT_SMALL)
    text_w, text_h = text_bbox[2] - text_bbox[0], text_bbox[3] - text_bbox[1]
    draw.text(((img_width - text_w) / 2, bar_y + (bar_height - text_h) / 2), progress_text, font=FONT_SMALL, fill=(255, 255, 255))

    # Save the final image to a buffer
    buffer = io.BytesIO()
    final_img.save(buffer, "PNG")
    buffer.seek(0)
    return buffer.getvalue()