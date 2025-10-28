# ui/overlay.py
import logging
from typing import List, Optional
from PIL import Image, ImageDraw, ImageFont
try:
    from PIL import Image, ImageDraw, ImageFont
    _HAS_PIL = True
except Exception:
    _HAS_PIL = False
import os
from pathlib import Path

from cogs import db_utils

logger = logging.getLogger(__name__)

def _compose_overlay_image(puzzle_meta: dict, collected: List[str]) -> Image.Image:
    rows = puzzle_meta.get("rows", 1)
    cols = puzzle_meta.get("cols", 1)
    width = cols * 64
    height = rows * 64

    base_image = puzzle_meta.get("base_image") or puzzle_meta.get("full_image")
    base_image_path = str(Path(base_image)) if base_image else None
    logger.info("🧩 base_image resolved: %s", base_image_path)

    if base_image_path and os.path.exists(base_image_path):
        base = Image.open(base_image_path).convert("RGBA")
        logger.info("🧩 Loaded base image for %s", puzzle_meta.get("display_name", "unknown"))
    else:
        logger.warning("⚠️ Missing base image: %s", base_image_path)
        base = Image.new("RGBA", (width, height), (0, 0, 0, 255))
        logger.info("🧩 Using blank canvas for %s", puzzle_meta.get("display_name", "unknown"))

    # ✅ Skip grid entirely if no collected pieces
    if not collected:
        logger.info("🧩 No pieces collected — returning base image only")
        return base

    # ✅ Draw grid overlay only if collected
    draw = ImageDraw.Draw(base)
    draw.rectangle([0, 0, width - 1, height - 1], outline=(180, 180, 180, 255))

    collected_set = set(map(str, collected))
    logger.debug("🔍 Collected pieces: %s", collected_set)

    for r in range(rows):
        for c in range(cols):
            idx = r * cols + c + 1
            if str(idx) not in collected_set:
                continue  # ✅ Skip uncollected pieces — no shadows
            box = [c * 64 + 4, r * 64 + 4, (c + 1) * 64 - 4, (r + 1) * 64 - 4]
            draw.rectangle(box, fill=(0, 200, 100, 200))

    return base

def render_progress_image(
    puzzle_folder: str,
    collected_piece_ids: List[str],
    rows: int,
    cols: int,
    puzzle_config: Optional[dict],
    output_path: str,
    piece_map: dict[str, str],
    show_glow: bool = False,
    show_bar: bool = False
) -> None:
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    if not _HAS_PIL:
        with open(output_path, "wb") as fh:
            fh.write(b"")
        return

    width = cols * 128
    height = rows * 128
    img = render_base_layer(puzzle_folder, puzzle_config, width, height)

    try:
        font = ImageFont.load_default()
    except Exception:
        font = None
        logger.warning("⚠️ Failed to load default font")

    draw = ImageDraw.Draw(img)
    owned_set = set(map(str, collected_piece_ids))

    if owned_set:
        render_piece_overlay(img, draw, puzzle_folder, owned_set, rows, cols, font, show_glow)

    if show_bar:
        render_progress_bar(draw, width, height, len(owned_set), rows * cols)

    if len(owned_set) == rows * cols:
        full_path = os.path.join(puzzle_folder, f"{puzzle_config.get('display_name', puzzle_folder)}_full.png")
        if os.path.exists(full_path):
            img = Image.open(full_path).convert("RGBA").resize((width, height))
            logger.info("✅ Puzzle complete — using full image from %s", full_path)

    img.save(output_path)
    logger.info("🖼️ Preview image saved to %s", output_path)

def render_base_layer(puzzle_folder: str, puzzle_config: dict, width: int, height: int) -> Image.Image:
    base_name = puzzle_config.get("display_name", puzzle_folder)
    base_path = os.path.join(puzzle_folder, f"{base_name}_base.png")
    logger.info("Resolved base path: %s", base_path)

    if os.path.exists(base_path):
        img = Image.open(base_path).convert("RGBA")
        if img.size != (width, height):
            img = img.resize((width, height))
            logger.info("Resized base image to match canvas: %sx%s", width, height)
        else:
            logger.info("Base image already matches canvas size")
    else:
        img = Image.new("RGBA", (width, height), (30, 30, 30, 255))
        logger.warning("Base image missing — using blank canvas")

    return img

def render_piece_overlay(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    puzzle_folder: str,
    owned_set: set[str],
    rows: int,
    cols: int,
    font: Optional[ImageFont.ImageFont],
    show_glow: bool = False
) -> None:
    if not owned_set:
        logger.info("🧩 No owned pieces — skipping overlay entirely")
        return

    idx = 1
    for r in range(rows):
        for c in range(cols):
            piece_id = str(idx)
            x0 = c * 128
            y0 = r * 128
            piece_path = os.path.join(puzzle_folder, "pieces", f"p1_{piece_id}.png")

            if piece_id in owned_set:
                if not os.path.exists(piece_path):
                    logger.warning("⚠️ Skipping missing piece file: %s", piece_path)
                else:
                    if show_glow:
                        render_glow_effect(img, x0, y0)
                    piece_img = Image.open(piece_path).convert("RGBA")
                    img.alpha_composite(piece_img.resize((128, 128)), (x0, y0))
                    logger.info("🧩 Overlayed piece %s at (%s, %s)", piece_id, x0, y0)

            idx += 1

def render_progress_bar(draw: ImageDraw.ImageDraw, width: int, height: int, collected: int, total: int) -> None:
    bar_height = 20
    bar_y = height - bar_height - 10
    ratio = collected / total if total else 0
    fill_width = int(width * ratio)

    draw.rectangle([0, bar_y, width, bar_y + bar_height], fill=(50, 50, 50, 180))  # background
    draw.rectangle([0, bar_y, fill_width, bar_y + bar_height], fill=(0, 255, 0, 255))  # filled portion

    try:
        draw.text((width // 2 - 30, bar_y - 22), f"{collected}/{total}", fill=(255, 255, 255, 200))
    except Exception:
        logger.warning("⚠️ Failed to draw progress bar label")

    logger.info("Progress bar rendered: %d/%d", collected, total)

def render_glow_effect(img: Image.Image, x0: int, y0: int, size: int = 128) -> None:
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse([10, 10, size - 10, size - 10], fill=(0, 255, 255, 80))  # cyan glow
    img.alpha_composite(glow, (x0, y0))
    logger.info("Glow effect applied at (%d, %d)", x0, y0)

def render_progress_image(
    puzzle_folder: str,
    collected_piece_ids: List[str],
    rows: int,
    cols: int,
    puzzle_config: Optional[dict],
    output_path: str,
    piece_map: dict[str, str],
    show_glow: bool = False,
    show_bar: bool = False
) -> None:
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    width = cols * 128
    height = rows * 128
    img = render_base_layer(puzzle_folder, puzzle_config, width, height)

    try:
        font = ImageFont.load_default()
    except Exception:
        font = None
        logger.warning("⚠️ Failed to load default font")

    draw = ImageDraw.Draw(img)
    owned_set = set(map(str, collected_piece_ids))

    if owned_set:
        render_piece_overlay(img, draw, puzzle_folder, owned_set, rows, cols, font, show_glow)

    if show_bar:
        render_progress_bar(draw, width, height, len(owned_set), rows * cols)

    if len(owned_set) == rows * cols:
        full_path = os.path.join(puzzle_folder, f"{puzzle_config.get('display_name', puzzle_folder)}_full.png")
        if os.path.exists(full_path):
            img = Image.open(full_path).convert("RGBA").resize((width, height))
            logger.info("✅ Puzzle complete — using full image from %s", full_path)

    img.save(output_path)
    logger.info("🖼️ Preview image saved to %s", output_path)

def build_puzzle_progress(puzzle_key: str, collected: List[str], data: dict, user_id: Optional[str] = None) -> str:
    meta, slug = db_utils.get_puzzle(data, puzzle_key)
    if not meta:
        raise KeyError(f"Puzzle not found for key: {puzzle_key} (resolved slug: {slug})")
    img = _compose_overlay_image(meta, collected)
    out = db_utils.write_preview(slug, img, user_id)
    logger.info("build_puzzle_progress wrote preview: %s", out)

    return out
