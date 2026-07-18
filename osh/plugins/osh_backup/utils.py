"""Backup utility helpers."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path


def _now_stamp() -> str:
    """Return an ISO-ish timestamp suitable for filenames."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _safe_name(value: str | Path) -> str:
    """Return *value* with characters unsafe for filenames replaced."""
    text = str(value)
    # Keep a limited set of safe characters and collapse runs.
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", text).strip("_")
