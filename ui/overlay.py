# ui/overlay.py
import logging
from typing import List, Optional
from PIL import Image, ImageDraw

from cogs import db_utils

logger = logging.getLogger(__name__)

def _compose_overlay_image(puzzle_meta: dict, collected: List[str]) -> Image.Image:
    rows = puzzle_meta.get("rows", 1)
    cols = puzzle_meta.get("cols", 1)
    width = cols * 64
    height = rows * 64
    base = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(base)
    draw.rectangle([0, 0, width - 1, height - 1], outline=(180, 180, 180, 255))
    # simple visual for collected pieces: fill a portion of the grid
    try:
        collected_set = set(map(str, collected))
    except Exception:
        collected_set = set()
    for r in range(rows):
        for c in range(cols):
            idx = r * cols + c + 1
            box = [c * 64 + 4, r * 64 + 4, (c + 1) * 64 - 4, (r + 1) * 64 - 4]
            if str(idx) in collected_set:
                draw.rectangle(box, fill=(0, 200, 100, 200))
            else:
                draw.rectangle(box, fill=(60, 60, 60, 40))
    return base

def build_puzzle_progress(puzzle_key: str, collected: List[str], data: dict, user_id: Optional[str] = None) -> str:
    meta, slug = db_utils.get_puzzle(data, puzzle_key)
    if not meta:
        raise KeyError(f"Puzzle not found for key: {puzzle_key} (resolved slug: {slug})")
    img = _compose_overlay_image(meta, collected)
    out = db_utils.write_preview(slug, img, user_id)
    logger.info("build_puzzle_progress wrote preview: %s", out)
    return out
