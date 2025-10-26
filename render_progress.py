# render_progress.py
import os
from typing import Iterable, Optional
from PIL import Image

def _ensure_image(path: Optional[str]) -> bool:
    return bool(path and os.path.isfile(path))

def render_progress_image(
    puzzle_folder: str,
    collected_piece_ids: Iterable[str],
    rows: int,
    cols: int,
    base_image_name: Optional[str] = None,
    resize_to: Optional[tuple[int, int]] = None,
    pieces_subfolder: str = "pieces",
    piece_prefix: str = "p1_",
    output_path: Optional[str] = None,
    puzzle_config: Optional[dict] = None,
) -> str:
    """
    Render collected pieces over the base image and save progress_preview.png in the puzzle folder by default.

    - puzzle_folder: path to the puzzle folder (e.g., puzzles/Alice Test)
    - collected_piece_ids: iterable of piece ids as strings e.g. ["1","3","16"]
    - rows, cols: grid used when slicing
    - puzzle_config: optional dict with 'slice_size' like [W, H] for exact geometry
    - resize_to overrides puzzle_config slice_size
    - returns path to saved preview PNG
    """
    # 1) Resolve base image (prefer *_base, fallback to *_full)
    base_path = None
    if base_image_name:
        base_path = os.path.join(puzzle_folder, base_image_name)
    else:
        for f in os.listdir(puzzle_folder):
            lf = f.lower()
            if lf.endswith((".png", ".jpg", ".jpeg")) and "_base." in lf:
                base_path = os.path.join(puzzle_folder, f)
                break
        if not base_path:
            for f in os.listdir(puzzle_folder):
                lf = f.lower()
                if lf.endswith((".png", ".jpg", ".jpeg")) and "_full." in lf:
                    base_path = os.path.join(puzzle_folder, f)
                    break
    if not _ensure_image(base_path):
        raise FileNotFoundError(f"No base/full image found in folder: {puzzle_folder}")

    # 2) Determine target_size from puzzle_config or resize_to
    target_size = None
    if puzzle_config and isinstance(puzzle_config.get("slice_size"), (list, tuple)) and len(puzzle_config["slice_size"]) == 2:
        try:
            target_size = (int(puzzle_config["slice_size"][0]), int(puzzle_config["slice_size"][1]))
        except Exception:
            target_size = None
    if resize_to:
        target_size = tuple(resize_to)

    # 3) Open base image and optionally resize to target_size
    with Image.open(base_path) as im:
        base = im.convert("RGBA")
        if target_size:
            base = base.resize(target_size, Image.Resampling.LANCZOS)

        width, height = base.size
        piece_w = width // cols
        piece_h = height // rows

        # crop to exact grid area to avoid fractional cell pixels
        base = base.crop((0, 0, piece_w * cols, piece_h * rows))

        composite = Image.new("RGBA", base.size)
        composite.paste(base, (0, 0))

        pieces_dir = os.path.join(puzzle_folder, pieces_subfolder)
        for pid in collected_piece_ids:
            try:
                idx = int(pid)
            except Exception:
                continue
            if idx < 1 or idx > rows * cols:
                continue
            row = (idx - 1) // cols
            col = (idx - 1) % cols
            px = col * piece_w
            py = row * piece_h

            piece_filename = f"{piece_prefix}{idx}.png"
            piece_path = os.path.join(pieces_dir, piece_filename)

            # tolerant fallback: try a filename prefixed with folder basename
            if not _ensure_image(piece_path):
                alt = os.path.join(pieces_dir, f"{os.path.basename(puzzle_folder)}_{piece_filename}")
                if _ensure_image(alt):
                    piece_path = alt
                else:
                    # piece missing: skip (leave base visible)
                    continue

            with Image.open(piece_path) as piece_im:
                piece = piece_im.convert("RGBA")
                # resize piece to exact cell size if needed
                if piece.size != (piece_w, piece_h):
                    piece = piece.resize((piece_w, piece_h), Image.Resampling.LANCZOS)
                composite.paste(piece, (px, py), piece)

    out = output_path or os.path.join(puzzle_folder, "progress_preview.png")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    composite.save(out)
    return out

