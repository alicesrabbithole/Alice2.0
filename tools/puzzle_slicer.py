#!/usr/bin/env python3
"""
Slice a source image and set up puzzle files for the bot.

This version adds an automatic sanity check after slicing that compares each
piece's dimensions to the expected piece size and reports warnings when pieces
are unexpectedly small or vary significantly.

See the earlier tool for full usage examples. New CLI flags:
  --min_fraction FLOAT   : fraction of expected piece size under which to warn (default 0.5)
  --min_pixels INT       : absolute minimum width/height in pixels under which to warn (default 32)

Warnings are printed to stdout and embedded into qa.html so you can inspect them
in the preview QA page.
"""
import os
from pathlib import Path
import json
import re
import tempfile
import shutil
import subprocess
import sys
import argparse
from math import ceil
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

def slugify(name: str) -> str:
    name = name.lower().replace(" ", "_")
    name = re.sub(r"[^\w_]", "", name)
    return re.sub(r"_+", "_", name).strip("_")

def slice_puzzle(image_path: Path, output_dir: Path, rows: int, cols: int, zero_pad: bool = True) -> int:
    """
    Slice image into rows x cols distributing remainder pixels across first rows/cols.
    Returns number of pieces written.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    with Image.open(image_path).convert("RGBA") as img:
        width, height = img.size
        base_w, extra_w = divmod(width, cols)
        base_h, extra_h = divmod(height, rows)
        count = 1
        pad = max(2, len(str(rows * cols)))
        for r in range(rows):
            top = r * base_h + min(r, extra_h)
            h = base_h + (1 if r < extra_h else 0)
            for c in range(cols):
                left = c * base_w + min(c, extra_w)
                w = base_w + (1 if c < extra_w else 0)
                right, lower = left + w, top + h
                piece = img.crop((left, top, right, lower))
                filename = f"p{count:0{pad}d}.png" if zero_pad else f"p{count}.png"
                piece.save(output_dir / filename)
                count += 1
    return count - 1

def make_contact_sheet(pieces_dir: Path, rows: int, cols: int, tile_size: Optional[int] = None, show_labels: bool = True) -> Image.Image:
    """
    Build a contact sheet image representing the grid of pieces.
    If tile_size is None, infer from the first piece (max dimension).
    """
    pieces = sorted([p for p in pieces_dir.iterdir() if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg")])
    total = rows * cols
    pieces = pieces[:total] + [None] * max(0, total - len(pieces))
    if tile_size is None:
        tile_size = 96
        for p in pieces:
            if p:
                with Image.open(p) as i:
                    tile_size = max(96, max(i.size))
                break
    border = 6
    label_height = 18 if show_labels else 0
    w = cols * tile_size + (cols + 1) * border
    h = rows * (tile_size + label_height) + (rows + 1) * border
    sheet = Image.new("RGBA", (w, h), (24, 24, 24, 255))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None
    idx = 0
    for r in range(rows):
        for c in range(cols):
            x = border + c * (tile_size + border)
            y = border + r * (tile_size + label_height + border)
            p = pieces[idx]
            if p:
                with Image.open(p).convert("RGBA") as img:
                    img = img.resize((tile_size, tile_size), Image.Resampling.LANCZOS)
                    sheet.paste(img, (x, y), img)
            if show_labels:
                label = f"p{idx+1}"
                draw.text((x + 3, y + tile_size + 1), label, fill=(230,230,230,255), font=font)
            idx += 1
    return sheet

def compute_tile_size_from_full(full_img_path: Path, cols: int, requested_tile_size: Optional[int]) -> int:
    """
    Determine tile_size for rendering progress images:
    - If requested_tile_size supplied, use it
    - Else use floor(full.width / cols)
    """
    with Image.open(full_img_path) as full:
        if requested_tile_size:
            return requested_tile_size
        return max(1, full.width // cols)

def render_progress_images(pieces_dir: Path, base_img_path: Path, full_img_path: Path, rows: int, cols: int,
                           tile_size: int, output_dir: Path, samples: List[int]) -> List[Path]:
    """
    Create composed progress images for each sample (list of counts, e.g., [0, 5, 12, 49]).
    Save as progress_{count:03d}.png in output_dir and return list of saved Paths.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    bg_path = base_img_path if base_img_path and base_img_path.exists() else full_img_path
    saved = []
    piece_files = sorted([p for p in pieces_dir.iterdir() if p.is_file() and p.suffix.lower() == ".png"])
    def piece_index(p: Path) -> int:
        name = p.stem
        name = name.lstrip("pP")
        try:
            return int(name)
        except Exception:
            return 0
    piece_files.sort(key=piece_index)
    total_expected = rows * cols
    for count in samples:
        cnt = min(max(0, int(count)), len(piece_files), total_expected)
        with Image.open(bg_path).convert("RGBA") as bg:
            composed = bg.resize((cols * tile_size, rows * tile_size), Image.Resampling.LANCZOS)
            for idx in range(cnt):
                try:
                    p = piece_files[idx]
                except IndexError:
                    break
                with Image.open(p).convert("RGBA") as piece:
                    piece_resized = piece.resize((tile_size, tile_size), Image.Resampling.LANCZOS)
                    pos = idx
                    r, c = divmod(pos, cols)
                    composed.alpha_composite(piece_resized, (c * tile_size, r * tile_size))
            out_path = output_dir / f"progress_{cnt:03d}.png"
            composed.save(out_path)
            saved.append(out_path)
    return saved

def sanity_check_pieces(pieces_dir: Path, expected_w: int, expected_h: int, rows: int, cols: int,
                        min_fraction: float = 0.5, min_pixels: int = 32) -> List[str]:
    """
    Check each piece in pieces_dir against expected piece size (expected_w x expected_h).
    - Warn if width or height < max(min_pixels, expected_* * min_fraction)
    - Warn if any piece deviates from median size by > 30%
    Returns list of warning strings.
    """
    warnings: List[str] = []
    piece_files = sorted([p for p in pieces_dir.iterdir() if p.is_file() and p.suffix.lower() == ".png"])
    if not piece_files:
        warnings.append("No piece images found to sanity-check.")
        return warnings

    sizes = []
    for p in piece_files:
        try:
            with Image.open(p) as im:
                sizes.append((p.name, im.width, im.height))
        except Exception as e:
            warnings.append(f"Could not open piece {p.name}: {e}")

    # thresholds
    thresh_w = max(min_pixels, int(expected_w * min_fraction))
    thresh_h = max(min_pixels, int(expected_h * min_fraction))

    # check absolute smallness
    for name, w, h in sizes:
        if w < thresh_w or h < thresh_h:
            warnings.append(f"Piece {name} is small ({w}x{h}) — expected approx {expected_w}x{expected_h}; threshold {thresh_w}x{thresh_h}.")

    # check consistency vs median
    ws = sorted([w for (_, w, _) in sizes])
    hs = sorted([h for (_, _, h) in sizes])
    if ws and hs:
        median_w = ws[len(ws)//2]
        median_h = hs[len(hs)//2]
        for name, w, h in sizes:
            if median_w > 0 and (abs(w - median_w) / median_w) > 0.30:
                warnings.append(f"Piece {name} width {w}px differs from median {median_w}px by >30%.")
            if median_h > 0 and (abs(h - median_h) / median_h) > 0.30:
                warnings.append(f"Piece {name} height {h}px differs from median {median_h}px by >30%.")

    # if number of files doesn't match rows*cols
    expected_count = rows * cols
    if len(piece_files) != expected_count:
        warnings.append(f"Piece count mismatch: found {len(piece_files)} pieces but expected {expected_count} ({rows}x{cols}).")

    return warnings

def generate_qa_html(puzzle_root: Path, pieces_dir: Path, preview_img: Path, progress_imgs: List[Path], meta: dict, warnings: List[str]) -> Path:
    """
    Create a simple QA HTML page inside puzzle_root (qa.html) that shows:
    - preview (contact sheet)
    - list/grid of piece thumbnails with filenames
    - progress sample images
    - warnings (if any)
    """
    html_path = puzzle_root / "qa.html"
    rows = meta.get("rows")
    cols = meta.get("cols")
    title = meta.get("display_name", puzzle_root.name)
    pieces = sorted([p.name for p in pieces_dir.iterdir() if p.is_file() and p.suffix.lower() == ".png"])
    parts = []
    parts.append(f"<html><head><meta charset='utf-8'><title>QA: {title}</title></head><body style='background:#121212;color:#eee;font-family:Segoe UI,Roboto,Helvetica,Arial;'>")
    parts.append(f"<h1>QA: {title}</h1>")

    if warnings:
        parts.append("<h2 style='color:#ffb86b'>Sanity check warnings</h2>")
        parts.append("<ul>")
        for w in warnings:
            parts.append(f"<li style='color:#ffcccc'>{w}</li>")
        parts.append("</ul>")

    parts.append(f"<h2>Preview contact sheet</h2>")
    parts.append(f"<img src='{preview_img.name}' alt='preview' style='max-width:100%;border:1px solid #333'/>")
    parts.append("<h2>Pieces</h2>")
    parts.append("<div style='display:flex;flex-wrap:wrap;gap:8px'>")
    for p in pieces:
        parts.append(f"<div style='width:140px;text-align:center;background:#1b1b1b;padding:6px;border-radius:6px'><img src='pieces/{p}' style='width:128px;height:128px;object-fit:contain;border:1px solid #222' /><div style='margin-top:6px;font-size:12px;color:#ccc'>{p}</div></div>")
    parts.append("</div>")
    parts.append("<h2>Progress samples</h2>")
    parts.append("<div style='display:flex;flex-wrap:wrap;gap:12px'>")
    for p in progress_imgs:
        parts.append(f"<div style='background:#1b1b1b;padding:6px;border-radius:6px'><img src='{p.name}' style='width:256px;height:auto;border:1px solid #222' /><div style='text-align:center;margin-top:6px;color:#ccc'>{p.name}</div></div>")
    parts.append("</div>")
    parts.append("<hr/><p style='color:#999;font-size:12px'>Generated by slicer preview</p>")
    parts.append("</body></html>")
    html_path.write_text("\n".join(parts), encoding="utf-8")
    return html_path

def open_with_default(path: Path):
    try:
        if sys.platform.startswith("darwin"):
            subprocess.run(["open", str(path)])
        elif os.name == "nt":
            os.startfile(str(path))
        else:
            subprocess.run(["xdg-open", str(path)])
    except Exception as e:
        print("Could not open automatically:", e)

def write_meta(puzzle_root: Path, display_name: str, rows: int, cols: int, tile_size: Optional[int]) -> dict:
    meta = {"display_name": display_name, "rows": rows, "cols": cols}
    if tile_size:
        meta["tile_size"] = int(tile_size)
    meta_path = puzzle_root / "meta.json"
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta

def main():
    parser = argparse.ArgumentParser(description="Slice a puzzle image and optionally preview and finalize.")
    parser.add_argument("display_name", type=str)
    parser.add_argument("full_image", type=Path)
    parser.add_argument("base_image", type=Path)
    parser.add_argument("--grid", type=str, default="4x4")
    parser.add_argument("--puzzles_dir", type=Path, default=Path("puzzles"))
    parser.add_argument("--preview", action="store_true", help="Run in preview mode (use temp dir).")
    parser.add_argument("--open", action="store_true", help="Open QA/preview after generation.")
    parser.add_argument("--tile_size", type=int, default=None, help="Suggested tile_size to include in meta.json and use in previews.")
    parser.add_argument("--finalize", action="store_true", help="Move generated folder into puzzles_dir (works with --preview or direct run).")
    parser.add_argument("--force", action="store_true", help="When finalizing, overwrite existing target if present.")
    parser.add_argument("--progress_samples", type=str, default="0,25,50,75,100", help="Comma-separated percentages to render progress samples (e.g. 0,10,50,100) or absolute counts if you prefix with 'c:'.")
    parser.add_argument("--min_fraction", type=float, default=0.5, help="Fraction of expected piece size below which to warn (default 0.5).")
    parser.add_argument("--min_pixels", type=int, default=32, help="Absolute minimum pixels for width/height below which to warn.")
    args = parser.parse_args()

    try:
        rows, cols = map(int, args.grid.split("x"))
    except Exception:
        print("Invalid grid format. Use RowsxCols like 7x7")
        return

    puzzle_slug = slugify(args.display_name)
    if args.preview:
        tmp = Path(tempfile.mkdtemp(prefix=f"puzzle_preview_{puzzle_slug}_"))
        puzzle_root = tmp / puzzle_slug
    else:
        puzzle_root = args.puzzles_dir / puzzle_slug

    pieces_dir = puzzle_root / "pieces"
    puzzle_root.mkdir(parents=True, exist_ok=True)

    dest_full = puzzle_root / f"{puzzle_slug}_full.png"
    dest_base = puzzle_root / f"{puzzle_slug}_base.png"
    shutil.copy2(args.full_image, dest_full)
    shutil.copy2(args.base_image, dest_base)

    meta = write_meta(puzzle_root, args.display_name, rows, cols, args.tile_size)

    num = slice_puzzle(dest_full, pieces_dir, rows, cols, zero_pad=True)
    print(f"Sliced full image -> {num} pieces at {pieces_dir}")

    # Sanity check pieces
    with Image.open(dest_full) as fimg:
        expected_w = fimg.width // cols
        expected_h = fimg.height // rows

    warnings = sanity_check_pieces(pieces_dir, expected_w, expected_h, rows, cols, min_fraction=args.min_fraction, min_pixels=args.min_pixels)
    if warnings:
        print("\n=== SANITY CHECK WARNINGS ===")
        for w in warnings:
            print(" -", w)
        print("=== END WARNINGS ===\n")
    else:
        print("Sanity check: no issues found with piece dimensions.")

    sheet = make_contact_sheet(pieces_dir, rows, cols, tile_size=args.tile_size or None)
    preview_path = puzzle_root / "preview.png"
    sheet.save(preview_path)
    print("Saved contact sheet preview:", preview_path)

    samples_arg = args.progress_samples.strip()
    samples: List[int] = []
    if samples_arg.startswith("c:"):
        try:
            samples = [int(x) for x in samples_arg[2:].split(",") if x.strip()]
        except Exception:
            samples = []
    else:
        try:
            percents = [float(x) for x in samples_arg.split(",") if x.strip()]
            total = rows * cols
            samples = sorted(list({min(total, max(0, round(total * p / 100.0))) for p in percents}))
        except Exception:
            samples = [0, num]

    tile_size = compute_tile_size_from_full(dest_full, cols, args.tile_size)
    progress_dir = puzzle_root
    progress_images = render_progress_images(pieces_dir, dest_base, dest_full, rows, cols, tile_size, progress_dir, samples)
    print("Rendered progress sample images:", ", ".join(p.name for p in progress_images))

    qa_html = generate_qa_html(puzzle_root, pieces_dir, preview_path, progress_images, meta, warnings)
    print("QA page written to:", qa_html)

    if args.finalize:
        target_dir = args.puzzles_dir / puzzle_slug
        if target_dir.exists():
            if not args.force:
                print(f"Target {target_dir} already exists. Use --force to overwrite.")
                return
            else:
                if target_dir.is_dir():
                    shutil.rmtree(target_dir)
                else:
                    target_dir.unlink()
        args.puzzles_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(puzzle_root), str(target_dir))
        print(f"Finalized: moved {puzzle_root} -> {target_dir}")
        if args.open:
            open_with_default(target_dir / "qa.html")
        return

    if args.preview:
        print("\nPreview complete. Nothing has been written to your puzzles directory.")
        print("To finalize, either re-run without --preview, or move the preview folder manually.")
        print(f"Preview folder: {puzzle_root}")
        if args.open:
            open_with_default(qa_html)
        return

    print(f"Puzzle created at: {puzzle_root}")
    if args.open:
        open_with_default(qa_html)

if __name__ == "__main__":
    main()