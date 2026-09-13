"""Module fingerprinting for ``osh update``.

Computes a stable SHA-256 digest per project module covering only code and
data files — the files whose changes require a ``-u`` module update. Static
assets (``static/``), caches (``__pycache__``) and dot-entries are ignored,
since changing them does not need a module update.
"""

import hashlib
import os
from pathlib import Path

from ..common import discover_addons_paths

_HASHED_SUFFIXES = {".py", ".xml", ".csv", ".po", ".pot", ".yaml", ".yml", ".sql"}
_SKIP_DIRS = {"__pycache__", "static"}
_UPSTREAM_REPO_NAMES = {"odoo", "enterprise", "design-themes"}


def fingerprint_module(module_dir):
    """Return a SHA-256 digest of *module_dir*'s code and data files."""
    entries = []
    for path in _iter_fingerprint_files(Path(module_dir)):
        rel = path.relative_to(module_dir).as_posix()
        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        entries.append((rel, file_hash))
    digester = hashlib.sha256()
    for rel, file_hash in sorted(entries):
        digester.update(rel.encode())
        digester.update(b"\0")
        digester.update(file_hash.encode())
        digester.update(b"\0")
    return digester.hexdigest()


def fingerprint_project_modules(base, *, skip_nested=False, upstream=True):
    """Return ``{module_name: digest}`` for every module under *base*."""
    addons = discover_modules(base, skip_nested=skip_nested, upstream=upstream)
    return {name: fingerprint_module(path) for name, path in addons.items()}


def fingerprint_modules_by_name(base, names, *, skip_nested=False):
    """Return ``{name: digest}`` for the discovered modules among *names*."""
    addons = discover_modules(base, skip_nested=skip_nested)
    return {n: fingerprint_module(addons[n]) for n in names if n in addons}


def discover_modules(base, *, skip_nested=False, upstream=True):
    """Return ``{module_name: module_dir}`` for discovered modules under *base*.

    With *skip_nested*, modules living inside a nested git repository — such as
    ``odoo``/``enterprise`` source checkouts or git submodules — are skipped.
    With *upstream* false, only modules inside the upstream Odoo trees
    (``odoo``, ``enterprise``, ``design-themes`` repos) are skipped.
    """
    modules = {}
    for addon in discover_addons_paths(base):
        repo = _nested_repo_root(addon, base)
        if repo and (
            skip_nested or (not upstream and repo.name in _UPSTREAM_REPO_NAMES)
        ):
            continue
        modules[addon.name] = addon
    return modules


def _nested_repo_root(module_dir, base):
    """Return the innermost git repo dir containing *module_dir* below *base*."""
    base = Path(base).resolve()
    current = Path(module_dir).resolve().parent
    while current != base and base in current.parents:
        if (current / ".git").exists():
            return current
        current = current.parent
    return None


def _iter_fingerprint_files(module_dir):
    """Yield code/data files under *module_dir* relevant to module updates."""
    for root, dirs, files in os.walk(module_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in _SKIP_DIRS]
        for name in files:
            if name.startswith("."):
                continue
            if Path(name).suffix.lower() in _HASHED_SUFFIXES:
                yield Path(root) / name
