import logging
from typing import List, Dict
from PIL import Image, ImageDraw, ImageFont
import io
from pathlib import Path

logger = logging.getLogger(__name__)

# --- FIX 1: We still need a font ---
FONT_NAME = "DejaVuSans-Bold.ttf"
try:
    FONT = ImageFont.truetype(FONT_NAME, 24)
    FONT_SMALL = ImageFont.truetype(FONT_NAME, 18)
except IOError:
    logger.warning(f"Font not found at '{FONT_NAME}'. Falling back to default font.")
    FONT = ImageFont.load_default()
    FONT_SMALL = ImageFont.load_default()


# --- FIX 2 (THE SLEDGEHAMMER): REMOVE THE PUZZLES_ROOT CONSTANT ---
# We will no longer prepend a root directory. We will use the path from the data file as-is.

def render_progress_image(bot_data: Dict, puzzle_key: str, collected_piece_ids: List[str]) -> bytes:
    """Renders a user's puzzle progress and a progress bar into a single image."""
    puzzle_meta = bot_data.get("puzzles", {}).get(puzzle_key)
    piece_map = bot_data.get("pieces", {}).get(puzzle_key)

    if not puzzle_meta or not piece_map:
        raise FileNotFoundError(f"Puzzle data for '{puzzle_key}' is missing. Please run /syncpuzzles.")

    rows, cols = puzzle_meta.get("rows", 4), puzzle_meta.get("cols", 4)
    tile_size = 96
    img_width, img_height = cols * tile_size, rows * tile_size
    bar_height = 30
    padding = 5
    total_height = img_height + bar_height + (padding * 2)

    final_img = Image.new("RGBA", (img_width, total_height), (49, 51, 56, 255))
    draw = ImageDraw.Draw(final_img)

    puzzle_img = Image.new("RGBA", (img_width, img_height), (30, 30, 30, 255))

    # --- FIX 3: Use the paths DIRECTLY from the data file ---
    base_image_path = puzzle_meta.get("base_image")
    try:
        if base_image_path and Path(base_image_path).exists():
            puzzle_img = Image.open(base_image_path).convert("RGBA").resize((img_width, img_height),
                                                                            Image.Resampling.LANCZOS)
        elif base_image_path:
            logger.warning(f"Base image for {puzzle_key} not found at: {base_image_path}")
    except Exception as e:
        logger.exception(f"Failed to load base puzzle image for {puzzle_key}. Error: {e}")

    for piece_id in collected_piece_ids:
        piece_path_str = piece_map.get(str(piece_id))
        try:
            if piece_path_str and Path(piece_path_str).exists():
                with Image.open(piece_path_str).convert("RGBA") as piece_img:
                    piece_img = piece_img.resize((tile_size, tile_size), Image.Resampling.LANCZOS)
                    idx = int(piece_id) - 1
                    r, c = divmod(idx, cols)
                    puzzle_img.paste(piece_img, (c * tile_size, r * tile_size), piece_img)
            elif piece_path_str:
                logger.warning(f"Piece {piece_id} for {puzzle_key} not found at: {piece_path_str}")
        except Exception as e:
            logger.exception(f"Failed to process piece {piece_id} for {puzzle_key}. Error: {e}")

    total_pieces = len(piece_map)
    is_complete = len(collected_piece_ids) == total_pieces
    if is_complete:
        full_image_path_str = puzzle_meta.get("full_image")
        try:
            if full_image_path_str and Path(full_image_path_str).exists():
                full_img = Image.open(full_image_path_str).convert("RGBA").resize((img_width, img_height),
                                                                                  Image.Resampling.LANCZOS)
                puzzle_img = full_img
            elif full_image_path_str:
                logger.warning(f"Full image for {puzzle_key} not found at: {full_image_path_str}")
        except Exception as e:
            logger.exception(f"Failed to load full puzzle image on completion for {puzzle_key}. Error: {e}")

    final_img.paste(puzzle_img, (0, 0))

    bar_y = img_height + padding
    ratio = len(collected_piece_ids) / total_pieces if total_pieces > 0 else 0
    draw.rectangle([padding, bar_y, img_width - padding, bar_y + bar_height], fill=(20, 20, 20, 200),
                   outline=(200, 200, 200, 150))
    if ratio > 0:
        draw.rectangle([padding, bar_y, padding + (img_width - (padding * 2)) * ratio, bar_y + bar_height],
                       fill=(88, 101, 242, 220))

    progress_text = f"{len(collected_piece_ids)} / {total_pieces}"
    try:
        text_bbox = draw.textbbox((0, 0), progress_text, font=FONT_SMALL)
        text_w, text_h = text_bbox[2] - text_bbox[0], text_bbox[3] - text_bbox[1]
        draw.text(((img_width - text_w) / 2, bar_y + (bar_height - text_h) / 2), progress_text, font=FONT_SMALL,
                  fill=(255, 255, 255))
    except Exception as e:
        logger.exception(f"Failed to draw progress text. Error: {e}")

    buffer = io.BytesIO()
    final_img.save(buffer, "PNG")
    buffer.seek(0)
    return buffer.getvalue()