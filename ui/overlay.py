import io
import logging
from typing import Dict, List

from PIL import Image as PILImage

import config

logger = logging.getLogger(__name__)


def render_progress_image(bot_data: Dict, puzzle_key: str, collected_piece_ids: List[str]) -> bytes:
    """
    Compose and return PNG bytes for the current progress image for a puzzle.

    This renderer overlays collected piece images onto the puzzle base (or the completed full image),
    and intentionally does NOT draw any progress bar underneath the image.

    - bot_data: the bot data structure (expects bot_data['puzzles'][puzzle_key] and bot_data['pieces'][puzzle_key])
    - puzzle_key: puzzle identifier string
    - collected_piece_ids: list of piece ids (strings) that the user has collected for this puzzle

    Returns PNG bytes.
    """
    meta = bot_data.get("puzzles", {}).get(puzzle_key, {}) or {}
    piece_map = bot_data.get("pieces", {}).get(puzzle_key, {}) or {}

    rows = int(meta.get("rows", 4))
    cols = int(meta.get("cols", 4))
    tile_size = int(meta.get("tile_size", 96)) if meta.get("tile_size") else 96

    img_width, img_height = cols * tile_size, rows * tile_size

    # Resolve base image (try base_image then full image as fallback)
    base_rel = meta.get("base_image")
    full_rel = meta.get("full_image")
    base_path = (config.PUZZLES_ROOT / base_rel) if base_rel else None
    alt_full_path = (config.PUZZLES_ROOT / full_rel) if full_rel else None

    base_path_to_use = None
    if base_path and base_path.exists():
        base_path_to_use = base_path
    elif alt_full_path and alt_full_path.exists():
        base_path_to_use = alt_full_path

    if base_path_to_use:
        logger.info("Trying to open base image at: %s", base_path_to_use)
        try:
            base_img = PILImage.open(base_path_to_use).convert("RGBA").resize((img_width, img_height), PILImage.Resampling.LANCZOS)
            logger.info("Loaded base image: %s", base_path_to_use)
        except Exception:
            logger.exception("Failed to open/resize base image: %s", base_path_to_use)
            base_img = PILImage.new("RGBA", (img_width, img_height), (30, 30, 30, 255))
    else:
        logger.warning("Base/full image not found for puzzle %s; using placeholder.", puzzle_key)
        base_img = PILImage.new("RGBA", (img_width, img_height), (30, 30, 30, 255))

    # Ensure collected_piece_ids are considered as numeric strings where appropriate.
    # Keep pieces that map to a path in piece_map; support both '1' and 'p1' styles if your piece_map uses them.
    collected_numeric = []
    for pid in collected_piece_ids or []:
        sid = str(pid)
        if sid in piece_map:
            collected_numeric.append(sid)
        else:
            # try with/without leading "p"
            alt = sid if not sid.startswith("p") else sid[1:]
            alt2 = f"p{sid}" if not sid.startswith("p") else sid
            if alt in piece_map:
                collected_numeric.append(alt)
            elif alt2 in piece_map:
                collected_numeric.append(alt2)
            else:
                logger.debug("No piece mapping entry for id %s in puzzle %s; skipping", pid, puzzle_key)

    # Paste each collected piece onto the base image at the correct grid location.
    for sid in collected_numeric:
        piece_rel = piece_map.get(sid)
        if not piece_rel:
            logger.debug("Piece mapping for id %s empty; skipping", sid)
            continue
        piece_full = config.PUZZLES_ROOT / piece_rel
        if not piece_full.exists():
            logger.debug("Piece image file not found for id %s at %s; skipping", sid, piece_full)
            continue

        try:
            logger.info("Trying to paste piece %s from %s", sid, piece_full)
            with PILImage.open(piece_full).convert("RGBA") as piece_img:
                piece_img = piece_img.resize((tile_size, tile_size), PILImage.Resampling.LANCZOS)
                # Determine placement: assume piece IDs are 1..N mapped left-to-right, top-to-bottom.
                # If your piece_map keys are 'p1' or similar, convert to int when possible.
                idx = None
                try:
                    # remove leading 'p' if present
                    numeric = int(str(sid).lstrip("p"))
                    idx = numeric - 1
                except Exception:
                    logger.debug("Could not interpret piece id %s as numeric index; placing at (0,0) fallback", sid)
                    idx = 0
                r, c = divmod(idx, cols)
                base_img.alpha_composite(piece_img, (c * tile_size, r * tile_size))
        except Exception:
            logger.exception("Failed to paste piece %s from %s", sid, piece_full)

    # If puzzle is complete, replace base_img with the full image (if available)
    total_pieces = len(piece_map)
    collected_count = len(collected_numeric)
    if total_pieces > 0 and collected_count >= total_pieces:
        if alt_full_path and alt_full_path.exists():
            try:
                logger.info("Trying to open completed puzzle image at: %s", alt_full_path)
                full_img = PILImage.open(alt_full_path).convert("RGBA").resize((img_width, img_height), PILImage.Resampling.LANCZOS)
                base_img = full_img
            except Exception:
                logger.exception("Failed to load completed puzzle image: %s", alt_full_path)

    # Final image: just the composed puzzle (no progress bar or text)
    buf = io.BytesIO()
    base_img.save(buf, format="PNG")
    buf.seek(0)
    return buf.getvalue()