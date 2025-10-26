# cogs/preview_cache.py
import os
import hashlib
from pathlib import Path
from typing import List, Optional

try:
    from PIL import Image, ImageDraw, ImageFont
    _HAS_PIL = True
except Exception:
    _HAS_PIL = False

CACHE_DIR = Path(os.getcwd()) / "cache" / "previews"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

def _safe_filename(*parts: str) -> str:
    joined = "__".join(map(str, parts))
    return hashlib.sha1(joined.encode("utf-8")).hexdigest() + ".png"

def preview_cache_path(puzzle_slug: str, user_id: str, owned_piece_ids: List[str]) -> str:
    fname = _safe_filename(puzzle_slug, user_id, ",".join(sorted(map(str, owned_piece_ids))))
    return str(CACHE_DIR.joinpath(fname))

def invalidate_user_puzzle_cache(puzzle_slug: str, user_id: str) -> int:
    removed = 0
    prefix = hashlib.sha1((puzzle_slug + "__" + user_id).encode("utf-8")).hexdigest()[:8]
    for p in CACHE_DIR.glob("*.png"):
        if prefix in p.name:
            try:
                p.unlink()
                removed += 1
            except Exception:
                pass
    return removed

def render_progress_image(
    puzzle_folder: str,
    collected_piece_ids: List[str],
    rows: int,
    cols: int,
    puzzle_config: Optional[dict],
    output_path: str,
) -> None:
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    if not _HAS_PIL:
        with open(output_path, "wb") as fh:
            fh.write(b"")
        return

    width = cols * 128
    height = rows * 128
    img = Image.new("RGBA", (width, height), (30, 30, 30, 255))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None

    owned_set = set(map(str, collected_piece_ids))
    idx = 1
    for r in range(rows):
        for c in range(cols):
            x0 = c * 128
            y0 = r * 128
            x1 = x0 + 128
            y1 = y0 + 128
            if str(idx) in owned_set:
                draw.rectangle([x0, y0, x1, y1], fill=(80, 180, 120, 255))
            else:
                draw.rectangle([x0, y0, x1, y1], outline=(200, 200, 200, 255), width=2)
            label = str(idx)
            draw.text((x0 + 8, y0 + 8), label, fill=(255, 255, 255, 255), font=font)
            idx += 1

    img.save(output_path)
