"""Project-local backup cache helpers.

The backup cache lives under ``.osh/backups`` and is shared by the
``osh backup`` and ``osh restore`` commands. These helpers are kept in
core so that both plugins can use them without depending on each other.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def get_cache_dir(base: Path) -> Path:
    """Return the backup cache directory for an Osh project."""
    return base / ".osh" / "backups"


def ensure_cache_dir(base: Path) -> Path:
    """Create the backup cache directory if it does not exist."""
    cache_dir = get_cache_dir(base)
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _metadata_path(backup_path: Path) -> Path:
    """Return the sidecar metadata path for a cached backup."""
    return Path(str(backup_path) + ".meta.json")


def write_metadata(
    backup_path: Path,
    *,
    source: str,
    original_format: str,
    created_at: str | None = None,
) -> None:
    """Write the metadata sidecar next to a cached backup."""
    created_at = created_at or datetime.now(timezone.utc).isoformat()
    meta = {
        "source": source,
        "format": original_format,
        "created_at": created_at,
        "path": str(backup_path),
    }
    _metadata_path(backup_path).write_text(json.dumps(meta, indent=2))


def read_metadata(backup_path: Path) -> dict:
    """Read metadata for a cached backup, falling back to file metadata."""
    meta_path = _metadata_path(backup_path)
    if meta_path.exists():
        try:
            return json.loads(meta_path.read_text())
        except json.JSONDecodeError:
            pass
    stat = backup_path.stat()
    return {
        "source": "unknown",
        "format": backup_path.suffix.lstrip("."),
        "created_at": datetime.fromtimestamp(
            stat.st_mtime, tz=timezone.utc
        ).isoformat(),
        "path": str(backup_path),
    }


def list_cache(base: Path, *, limit: int = 20, reverse: bool = False) -> list[dict]:
    """Return cached backups sorted newest first by default."""
    cache_dir = get_cache_dir(base)
    if not cache_dir.exists():
        return []

    backups: list[Path] = []
    for path in cache_dir.iterdir():
        if path.is_file() and not path.name.endswith(".meta.json"):
            backups.append(path)

    backups.sort(key=lambda p: p.stat().st_mtime, reverse=not reverse)

    result: list[dict] = []
    for idx, path in enumerate(backups[:limit], start=1):
        meta = read_metadata(path)
        result.append(
            {
                "id": idx,
                "filename": path.name,
                "source": meta.get("source", "unknown"),
                "created_at": meta.get("created_at", ""),
                "path": path,
            }
        )
    return result


def resolve_cache_id(base: Path, cache_id: int, *, limit: int = 20) -> Path:
    """Return the cached backup path for a 1-based cache ID."""
    entries = list_cache(base, limit=limit)
    for entry in entries:
        if entry["id"] == cache_id:
            return entry["path"]
    raise ValueError(f"Cache entry #{cache_id} not found.")
