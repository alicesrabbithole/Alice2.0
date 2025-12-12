#!/usr/bin/env python3
"""
Helper: render stocking image with auto-scaled grid positions.

Place this file at ui/stocking_render_helpers.py and import
render_stocking_image_auto(...) from it.

Improvements vs the snippet you pasted:
- Uses context managers to ensure images are closed.
- Uses ImageOps.contain to fit stickers into slots without mutating originals.
- Ensures output directory exists and writes atomically (temp file + rename).
- Adds logging for easier debugging.
- Accepts sticker file paths that may be absolute or relative to assets_dir.
"""
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import logging
import tempfile
import os

try:
    from PIL import Image, ImageOps
except Exception:  # Pillow missing
    Image = None  # type: ignore
    ImageOps = None  # type: ignore

logger = logging.getLogger(__name__)


def compute_grid_positions(
    template_size: Tuple[int, int],
    grid_cols: int,
    grid_rows: int,
    margin: int = 40,
    slot_padding: int = 8,
) -> List[Tuple[int, int, int, int]]:
    """
    Compute a grid of slot rectangles (x, y, w, h) inside the template image.
    Returns list of tuples (x, y, width, height) left-to-right, top-to-bottom.
    """
    tw, th = template_size
    available_w = max(0, tw - 2 * margin)
    available_h = max(0, th - 2 * margin)
    # integer division can leave a small unused edge; that's fine for slot placement
    slot_w = available_w // max(1, grid_cols)
    slot_h = available_h // max(1, grid_rows)

    slots: List[Tuple[int, int, int, int]] = []
    for r in range(grid_rows):
        for c in range(grid_cols):
            x = margin + c * slot_w + slot_padding
            y = margin + r * slot_h + slot_padding
            w = max(8, slot_w - slot_padding * 2)
            h = max(8, slot_h - slot_padding * 2)
            slots.append((x, y, w, h))
    return slots


def _resolve_asset_path(assets_dir: Path, file_ref: str) -> Optional[Path]:
    """
    Resolve sticker/buildable asset path. Accepts absolute paths or paths
    relative to assets_dir.
    """
    if not file_ref:
        return None
    p = Path(file_ref)
    if p.is_absolute():
        return p if p.exists() else None
    # try relative to assets_dir first
    candidate = assets_dir / file_ref
    if candidate.exists():
        return candidate
    # try relative to repo cwd
    candidate2 = Path.cwd() / file_ref
    if candidate2.exists():
        return candidate2
    return None


def render_stocking_image_auto(
    user_id: int,
    user_stickers: List[str],
    stickers_def: Dict[str, Dict],
    assets_dir: Path,
    template_name: str = "template.png",
    grid_cols: int = 4,
    grid_rows: int = 3,
    out_name: Optional[str] = None,
) -> Optional[Path]:
    """
    Renders the stocking image for a user and returns the path to the saved file.

    - user_stickers: list of sticker keys in the order they should appear (earliest -> first slot)
    - stickers_def: mapping sticker_key -> { "file": "...", ... }
    - assets_dir: Path to folder containing template + sticker pngs
    - grid_cols x grid_rows decides number of slots (default 4x3 = 12)
    - Returns Path to output PNG or None on failure.
    """
    if Image is None or ImageOps is None:
        logger.debug("render_stocking_image_auto: Pillow not available")
        return None

    template_path = assets_dir / template_name
    if not template_path.exists():
        logger.debug("render_stocking_image_auto: template not found at %s", template_path)
        return None

    try:
        with Image.open(template_path).convert("RGBA") as base:
            tw, th = base.size
            slots = compute_grid_positions(
                (tw, th), grid_cols, grid_rows, margin=max(8, int(tw * 0.05)), slot_padding=max(6, int(tw * 0.01))
            )

            out_path = assets_dir / (out_name or f"stocking_{user_id}.png")
            out_path.parent.mkdir(parents=True, exist_ok=True)

            # Work on a copy of the base image in memory
            canvas = base.copy()

            slot_index = 0
            for sticker_key in user_stickers:
                if slot_index >= len(slots):
                    break
                sdef = stickers_def.get(sticker_key)
                if not sdef:
                    slot_index += 1
                    continue

                sfile_ref = sdef.get("file") or sdef.get("path") or ""
                sticker_path = _resolve_asset_path(assets_dir, sfile_ref)
                if not sticker_path:
                    slot_index += 1
                    continue

                try:
                    with Image.open(sticker_path).convert("RGBA") as simg:
                        x, y, w, h = slots[slot_index]
                        # Fit sticker into slot while preserving aspect ratio (ImageOps.contain)
                        # Use a copy so original Image is not modified by thumbnail
                        fitted = ImageOps.contain(simg, (w, h), method=Image.LANCZOS)
                        paste_x = x + (w - fitted.width) // 2
                        paste_y = y + (h - fitted.height) // 2
                        canvas.paste(fitted, (int(paste_x), int(paste_y)), fitted)
                except Exception as exc:
                    logger.exception("render_stocking_image_auto: failed to place sticker %s (%s): %s", sticker_key, sticker_path, exc)
                slot_index += 1

            # Save atomically: write to temp file then rename
            try:
                fd, tmp_path = tempfile.mkstemp(suffix=".png", dir=str(out_path.parent))
                os.close(fd)
                tmp_path = Path(tmp_path)
                canvas.save(tmp_path, format="PNG")
                tmp_path.replace(out_path)
                return out_path
            except Exception as exc:
                logger.exception("render_stocking_image_auto: failed to save output %s: %s", out_path, exc)
                # cleanup tmp if present
                try:
                    if 'tmp_path' in locals() and tmp_path.exists():
                        tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass
                return None
    except Exception as exc:
        logger.exception("render_stocking_image_auto: failed to open template %s: %s", template_path, exc)
        return None