#!/usr/bin/env python3
"""
Utility to make a module/logger quiet by default using an environment variable.
Call set_quiet_logger(logger, env_var="MY_COG_QUIET") from the top of your cog module.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

def set_quiet_logger(logger: logging.Logger, env_var: Optional[str] = None, default_quiet: bool = True, quiet_level: int = logging.WARNING) -> None:
    """
    If env_var is set, read it and decide whether to quiet the logger.
    If env_var is None, use RUMBLE_LISTENER_QUIET if present, otherwise default_quiet.
    Accepted false values: "0", "false", "no" (case-insensitive).
    """
    name = env_var or "RUMBLE_LISTENER_QUIET"
    raw = os.environ.get(name)
    if raw is None:
        quiet = bool(default_quiet)
    else:
        quiet = str(raw).strip().lower() not in ("0", "false", "no")

    if quiet:
        logger.setLevel(quiet_level)