# cogs/puzzle_composer.py
import logging
from typing import Optional
from PIL import Image, ImageDraw

from cogs import db_utils

logger = logging.getLogger(__name__)

class PuzzleComposer:
    """
    Minimal, robust composer wrapper.
    - Accepts the loaded data dict (data) and an optional collected structure.
    - build(...) composes a preview Image and returns the filesystem path from write_preview.
    - All callers should treat the returned value as a path string.
    """

    def __init__(self, data: dict, collected: Optional[dict] = None):
        self.data = data or {}
        self.collected = collected or {}

    def _get_puzzle_meta(self, puzzle_key: str):
        # resolve canonical slug (db_utils.slugify_key is authoritative)
        key = db_utils.slugify_key(puzzle_key)
        meta, key = db_utils.get_puzzle(self.data, key)
        return key, meta

    def _compose_base_image(self, meta: dict) -> Image.Image:
        # Minimal placeholder composition logic if metadata doesn't provide a full image.
        # Replace or extend this with your real composition logic.
        rows = meta.get("rows", 1)
        cols = meta.get("cols", 1)
        thumb = meta.get("thumbnail")
        width = cols * 64
        height = rows * 64
        base = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(base)
        draw.rectangle([0, 0, width - 1, height - 1], outline=(200, 200, 200, 255))
        # If a thumbnail path exists, try to paste it; otherwise leave placeholder.
        if thumb:
            try:
                thumb_img = Image.open(thumb).convert("RGBA")
                thumb_img = thumb_img.resize((width, height))
                base.paste(thumb_img, (0, 0), thumb_img)
            except Exception:
                logger.exception("Failed to open or paste thumbnail %s", thumb)
        return base

    def build(self, puzzle_key: str, user_id: Optional[str] = None) -> str:
        """
        Compose the puzzle preview and return the path to the written preview image.
        Raises exceptions for unexpected failures so callers can see them.
        """
        slug, meta = self._get_puzzle_meta(puzzle_key)
        if not meta:
            raise KeyError(f"Puzzle not found for key: {puzzle_key} (resolved slug: {slug})")

        base = self._compose_base_image(meta)

        # Always use the central write_preview helper and return its result.
        out_path = db_utils.write_preview(slug, base, user_id)
        logger.info("PuzzleComposer.build wrote preview: %s", out_path)
        return out_path