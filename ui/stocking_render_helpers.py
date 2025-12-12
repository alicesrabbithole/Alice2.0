# Helper: render stocking image with auto-scaled grid positions
# Drop this into your cogs/ folder and import render_stocking_image_auto(...) from it,
# or copy the function into your existing StockingCog.

from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from PIL import Image
except Exception:  # Pillow missing
    Image = None  # type: ignore

def compute_grid_positions(template_size: Tuple[int, int], grid_cols: int, grid_rows: int,
                           margin: int = 40, slot_padding: int = 8) -> List[Tuple[int, int, int, int]]:
    """
    Compute a grid of slot rectangles (x, y, w, h) inside the template image.
    - template_size: (width, height)
    - grid_cols, grid_rows: number of columns/rows for sticker grid
    - margin: outer margin in pixels
    - slot_padding: padding inside each slot for sticker content
    Returns list of tuples (x, y, width, height) left-to-right, top-to-bottom.
    """
    tw, th = template_size
    available_w = max(0, tw - 2 * margin)
    available_h = max(0, th - 2 * margin)
    slot_w = available_w // grid_cols
    slot_h = available_h // grid_rows

    slots = []
    for r in range(grid_rows):
        for c in range(grid_cols):
            x = margin + c * slot_w + slot_padding
            y = margin + r * slot_h + slot_padding
            w = max(8, slot_w - slot_padding * 2)
            h = max(8, slot_h - slot_padding * 2)
            slots.append((x, y, w, h))
    return slots

def render_stocking_image_auto(user_id: int, user_stickers: List[str], stickers_def: Dict[str, Dict],
                               assets_dir: Path, template_name: str = "template.png",
                               grid_cols: int = 4, grid_rows: int = 3,
                               out_name: Optional[str] = None) -> Optional[Path]:
    """
    Renders the stocking image for a user and returns the path to the saved file.
    - user_stickers: list of sticker keys in the order they should appear (earliest -> first slot)
    - stickers_def: mapping sticker_key -> { "file": "...", "slots": n }
    - assets_dir: Path to folder containing template + sticker pngs
    - grid_cols x grid_rows decides number of slots (default 4x3 = 12)
    - Returns Path to output PNG or None on failure.
    """
    if Image is None:
        return None

    template_path = assets_dir / template_name
    if not template_path.exists():
        return None

    try:
        base = Image.open(template_path).convert("RGBA")
    except Exception:
        return None

    tw, th = base.size
    slots = compute_grid_positions((tw, th), grid_cols, grid_rows, margin=int(tw * 0.05), slot_padding=max(6, int(tw * 0.01)))

    out_path = assets_dir / (out_name or f"stocking_{user_id}.png")

    # Paste stickers into the slots in order, skipping missing assets
    slot_index = 0
    for sticker_key in user_stickers:
        if slot_index >= len(slots):
            break
        sdef = stickers_def.get(sticker_key)
        if not sdef:
            slot_index += 1
            continue
        sfile = assets_dir / sdef.get("file", "")
        if not sfile.exists():
            slot_index += 1
            continue
        try:
            simg = Image.open(sfile).convert("RGBA")
            x, y, w, h = slots[slot_index]
            # Fit sticker into slot while preserving aspect ratio
            simg.thumbnail((w, h), Image.LANCZOS)
            # Center sticker inside the slot
            paste_x = x + (w - simg.width) // 2
            paste_y = y + (h - simg.height) // 2
            base.paste(simg, (paste_x, paste_y), simg)
        except Exception:
            pass
        slot_index += 1

    try:
        base.save(out_path, format="PNG")
        return out_path
    except Exception:
        return None