import logging
from typing import Optional
from PIL import Image, ImageDraw
import os

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

    def _compose_overlay_image(self, meta: dict, collected: list) -> Image.Image:
        base_path = meta.get("base_image") or meta.get("full_image")
        if not base_path or not os.path.exists(base_path):
            raise FileNotFoundError(f"Missing base image: {base_path}")

        img = Image.open(base_path).convert("RGBA")

        if not collected:
            logger.info("🧩 No pieces collected — returning base image")
            return img

        # Optional: draw overlays if pieces are collected
        # You can wire in render_piece_overlay here if needed
        return img

    def build(self, puzzle_key: str, user_id: Optional[str] = None) -> str:
        slug, meta = self._get_puzzle_meta(puzzle_key)
        if not meta:
            raise KeyError(f"Puzzle not found for key: {puzzle_key} (resolved slug: {slug})")

        collected = []  # No progress — just show the base image
        base = self._compose_overlay_image(meta, collected)

        out_path = db_utils.write_preview(slug, base, user_id)
        logger.info("PuzzleComposer.build wrote preview: %s", out_path)
        return out_path
