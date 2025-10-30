import logging
from typing import List, Tuple, Dict
from PIL import Image, ImageDraw, ImageFont
import os
import io

logger = logging.getLogger(__name__)

try:
    FONT = ImageFont.truetype("arial.ttf", 24)
    FONT_SMALL = ImageFont.truetype("arial.ttf", 18)
except IOError:
    logger.warning("Arial font not found. Falling back to default font.")
    FONT = ImageFont.load_default()
    FONT_SMALL = ImageFont.load_default()


def render_progress_image(bot_data: Dict, puzzle_key: str, collected_piece_ids: List[str]) -> bytes:
    """Renders a user's puzzle progress and progress bar into a single image."""
    puzzle_meta = bot_data.get("puzzles", {}).get(puzzle_key, {})
    piece_map = bot_data.get("pieces", {}).get(puzzle_key, {})

    if not puzzle_meta or not piece_map:
        raise FileNotFoundError(f"Metadata or pieces for puzzle '{puzzle_key}' not found.")

    rows, cols = puzzle_meta.get("rows", 4), puzzle_meta.get("cols", 4)
    tile_size = 96
    img_width, img_height = cols * tile_size, rows * tile_size
    bar_height = 30
    total_height = img_height + bar_height + 10  # Add padding

    # Create the main canvas for the combined image
    final_img = Image.new("RGBA", (img_width, total_height), (49, 51, 56, 255))  # Discord bg color

    # --- 1. Draw the Puzzle Image ---
    base_image_path = puzzle_meta.get("base_image")
    if base_image_path and os.path.exists(base_image_path):
        puzzle_img = Image.open(base_image_path).convert("RGBA").resize((img_width, img_height),
                                                                        Image.Resampling.LANCZOS)
    else:
        puzzle_img = Image.new("RGBA", (img_width, img_height), (30, 30, 30, 255))

    for piece_id in collected_piece_ids:
        piece_path = piece_map.get(str(piece_id))
        if piece_path and os.path.exists(piece_path):
            with Image.open(piece_path).convert("RGBA") as piece_img:
                piece_img = piece_img.resize((tile_size, tile_size), Image.Resampling.LANCZOS)
                idx = int(piece_id) - 1
                r, c = divmod(idx, cols)
                puzzle_img.paste(piece_img, (c * tile_size, r * tile_size), piece_img)

    # If complete, show the full image
    total_pieces = len(piece_map)
    if len(collected_piece_ids) == total_pieces:
        full_path = puzzle_meta.get("full_image")
        if full_path and os.path.exists(full_path):
            puzzle_img = Image.open(full_path).convert("RGBA").resize((img_width, img_height), Image.Resampling.LANCZOS)

    # Paste the puzzle part onto the main canvas
    final_img.paste(puzzle_img, (0, 0))

    # --- 2. Draw the Progress Bar ---
    bar_y = img_height + 5
    draw = ImageDraw.Draw(final_img)
    ratio = len(collected_piece_ids) / total_pieces if total_pieces > 0 else 0

    # Bar background
    draw.rectangle([5, bar_y, img_width - 5, bar_y + bar_height], fill=(20, 20, 20, 200), outline=(200, 200, 200, 150))
    # Bar fill (Purple)
    if ratio > 0:
        draw.rectangle([5, bar_y, 5 + (img_width - 10) * ratio, bar_y + bar_height], fill=(88, 101, 242, 220))

    progress_text = f"{len(collected_piece_ids)} / {total_pieces}"
    text_bbox = draw.textbbox((0, 0), progress_text, font=FONT_SMALL)
    text_w, text_h = text_bbox[2] - text_bbox[0], text_bbox[3] - text_bbox[1]
    draw.text(((img_width - text_w) / 2, bar_y + (bar_height - text_h) / 2), progress_text, font=FONT_SMALL,
              fill=(255, 255, 255))

    # Save to buffer
    buffer = io.BytesIO()
    final_img.save(buffer, "PNG")
    buffer.seek(0)
    return buffer.getvalue()