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

    # --- 1. Always paste base image first ---
    base_image_path = puzzle_meta.get("base_image")
    canvas = Image.new("RGBA", (img_width, img_height), (30, 30, 30, 255))
    if base_image_path:
        full_path = config.PUZZLES_ROOT.joinpath(base_image_path)
        if full_path.exists():
            try:
                base = Image.open(full_path).convert("RGBA").resize((img_width, img_height),
                                                                    Image.Resampling.LANCZOS)
                canvas.paste(base, (0, 0), base)
            except Exception:
                logger.exception("Failed to load base image.")
    puzzle_img = canvas

    # --- 2. Paste collected pieces ---
    valid_ids = []
    for pid in collected_piece_ids:
        piece_path = piece_map.get(str(pid))
        if not piece_path:
            logger.debug(f"Piece ID {pid} not found in piece_map for puzzle {puzzle_key}")
            continue
        try:
            # Option A: use the stored relative path from piece_map
            full_piece_path = config.PUZZLES_ROOT / piece_path
            logger.debug(f"Opening piece {pid} at {full_piece_path}")

            if full_piece_path.exists():
                piece_img = Image.open(full_piece_path).convert("RGBA").resize(
                    (tile_size, tile_size),
                    Image.Resampling.LANCZOS
                )
                idx = int(pid) - 1
                r, c = divmod(idx, cols)
                puzzle_img.paste(piece_img, (c * tile_size, r * tile_size), piece_img)
                valid_ids.append(pid)
        except Exception:
            logger.exception(f"Failed to paste piece {pid}.")

    # Diagnostic log
    logger.info(f"User progress for {puzzle_key}: collected {len(valid_ids)} pieces -> {valid_ids}")

    # --- 3. Overlay full image if complete ---
    total_pieces = len(piece_map)
    if len(collected_piece_ids) == total_pieces:
        full_path = puzzle_meta.get("full_image")
        if full_path:
            try:
                full_img = Image.open(config.PUZZLES_ROOT.joinpath(full_path)).convert("RGBA").resize(
                    (img_width, img_height), Image.Resampling.LANCZOS)
                puzzle_img = full_img
            except Exception:
                logger.exception("Failed to load full image.")

    final_img.paste(puzzle_img, (0, 0))

    # --- 4. Progress bar ---
    bar_y = img_height + padding
    ratio = len(collected_piece_ids) / total_pieces if total_pieces > 0 else 0
    draw.rectangle([padding, bar_y, img_width - padding, bar_y + bar_height], fill=(20, 20, 20, 200),
                   outline=(200, 200, 200, 150))
    if ratio > 0:
        draw.rectangle([padding, bar_y, padding + (img_width - (padding * 2)) * ratio, bar_y + bar_height],
                       fill=(88, 101, 242, 220))

    progress_text = f"{len(collected_piece_ids)} / {total_pieces}"
    text_bbox = draw.textbbox((0, 0), progress_text, font=FONT_SMALL)
    text_w, text_h = text_bbox[2] - text_bbox[0], text_bbox[3] - text_bbox[1]
    draw.text(((img_width - text_w) / 2, bar_y + (bar_height - text_h) / 2), progress_text, font=FONT_SMALL,
              fill=(255, 255, 255))

    buffer = io.BytesIO()
    final_img.save(buffer, "PNG")
    buffer.seek(0)
    return buffer.getvalue()
