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
    logger.warning(f"Font not found at '{config.FONT_PATH}'. Falling back to default font.")
    FONT = ImageFont.load_default()
    FONT_SMALL = ImageFont.load_default()

def render_progress_image(bot_data: Dict, puzzle_key: str, collected_piece_ids: List[str]) -> bytes:
    """
    Renders a user's puzzle progress and a progress bar into a single image.
    - Overlays collected pieces over the base image.
    - Shows the completed puzzle image if complete.
    """
    puzzle_meta = bot_data.get("puzzles", {}).get(puzzle_key)
    piece_map = bot_data.get("pieces", {}).get(puzzle_key)

    if not puzzle_meta or not piece_map:
        raise FileNotFoundError(f"Metadata or pieces for puzzle '{puzzle_key}' not found.")

    rows = puzzle_meta.get("rows", 4)
    cols = puzzle_meta.get("cols", 4)
    tile_size = 96
    img_width, img_height = cols * tile_size, rows * tile_size
    bar_height = 30
    padding = 5
    total_height = img_height + bar_height + (padding * 2)

    # --- Base Puzzle Image ---
    base_image_path = puzzle_meta.get("base_image")
    base_full_path = config.PUZZLES_ROOT / base_image_path if base_image_path else None
    logger.info(f"Trying to open base image at: {base_full_path}")

    if base_full_path and base_full_path.exists():
        puzzle_img = Image.open(base_full_path).convert("RGBA").resize((img_width, img_height), Image.Resampling.LANCZOS)
        logger.info(f"Loaded base image: {base_full_path}")
    else:
        logger.error(f"Base image not found at: {base_full_path}")
        puzzle_img = Image.new("RGBA", (img_width, img_height), (30, 30, 30, 255))

    # --- Overlay Collected Pieces ---
    # Only process integer IDs to avoid grid confusion
    collected_pieces_numeric = [int(pid) for pid in collected_piece_ids if str(pid).isdigit()]
    for pid in collected_pieces_numeric:
        piece_id_str = str(pid)
        piece_path = piece_map.get(piece_id_str)
        piece_full_path = config.PUZZLES_ROOT / piece_path if piece_path else None
        logger.info(f"Trying to paste piece {piece_id_str} from {piece_full_path}")
        if piece_full_path and piece_full_path.exists():
            try:
                with Image.open(piece_full_path).convert("RGBA") as piece_img:
                    piece_img = piece_img.resize((tile_size, tile_size), Image.Resampling.LANCZOS)
                    # Correct grid placement: piece_id 1 at (0,0), piece_id 25 at (4,4)
                    idx = pid - 1
                    r, c = divmod(idx, cols)
                    puzzle_img.paste(piece_img, (c * tile_size, r * tile_size), piece_img)
            except Exception as ex:
                logger.exception(f"Failed to overlay piece {piece_id_str}: {ex}")
        else:
            logger.error(f"Missing piece image file: {piece_full_path}")

    # --- If Complete, Overlay the Completed Full Image ---
    total_pieces = len(piece_map)
    is_complete = len(collected_pieces_numeric) == total_pieces
    if is_complete:
        full_img_path = puzzle_meta.get("full_image")
        full_full_path = config.PUZZLES_ROOT / full_img_path if full_img_path else None
        logger.info(f"Trying to open completed puzzle image at: {full_full_path}")
        if full_full_path and full_full_path.exists():
            try:
                puzzle_img = Image.open(full_full_path).convert("RGBA").resize((img_width, img_height), Image.Resampling.LANCZOS)
            except Exception as ex:
                logger.exception(f"Failed to load completed puzzle image: {ex}")

    # --- Paste Puzzle + Draw Progress Bar ---
    final_img = Image.new("RGBA", (img_width, total_height), (49, 51, 56, 255))
    final_img.paste(puzzle_img, (0, 0))
    draw = ImageDraw.Draw(final_img)

    bar_y = img_height + padding
    ratio = len(collected_pieces_numeric) / total_pieces if total_pieces > 0 else 0

    # Bar background
    draw.rectangle([padding, bar_y, img_width - padding, bar_y + bar_height], fill=(20, 20, 20, 200), outline=(200, 200, 200, 150))
    # Bar fill
    if ratio > 0:
        draw.rectangle([padding, bar_y, padding + (img_width - (padding * 2)) * ratio, bar_y + bar_height], fill=(88, 101, 242, 220))

    # Progress text
    progress_text = f"{len(collected_pieces_numeric)} / {total_pieces} pieces"
    text_bbox = draw.textbbox((0, 0), progress_text, font=FONT_SMALL)
    text_w, text_h = text_bbox[2] - text_bbox[0], text_bbox[3] - text_bbox[1]
    draw.text(((img_width - text_w) / 2, bar_y + (bar_height - text_h) / 2), progress_text, font=FONT_SMALL, fill=(255, 255, 255))

    buffer = io.BytesIO()
    final_img.save(buffer, "PNG")
    buffer.seek(0)
    return buffer.getvalue()