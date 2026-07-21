"""Centralized Odoo version detection helpers."""

import re
import subprocess
from pathlib import Path

from . import echo
from .odoo_layout import find_odoo_executable


def detect_odoo_version(base, backend):
    """Return the installed Odoo version for *base* and *backend*, or None."""
    backend_name = (
        backend if isinstance(backend, str) else getattr(backend, "name", None)
    )

    if backend_name == "local":
        exe = find_odoo_executable(base)
        if exe:
            version = get_version_from_executable(exe)
            if version:
                return version
        return get_version_from_sources(base)

    if backend_name == "docker":
        version = get_version_from_sources(base)
        if version:
            return version

        from .plugins.osh_docker.utils import _COMPOSE_FILE, _load_docker_config

        cfg = _load_docker_config(base)
        compose_file = (cfg or {}).get("compose_file") or str(_COMPOSE_FILE)
        compose_path = base / Path(compose_file)
        if not compose_path.is_file():
            return None

        text = compose_path.read_text()
        match = re.search(r"image:\s*\S+/(odoo):(\S+)", text)
        if not match:
            match = re.search(r"image:\s*(odoo):(\S+)", text)
        if not match:
            return None

        tag = match.group(2)
        version_match = re.match(r"(\d+\.\d+)", tag)
        if version_match:
            return f"odoo {version_match.group(1)}"
        return None

    return None


def get_version_from_executable(exe):
    """Return the version reported by an Odoo executable, or None."""
    try:
        result = subprocess.run(
            [str(exe), "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, ValueError):
        return None

    output = (result.stdout or result.stderr or "").strip()
    if result.returncode != 0 or not output:
        return None
    return parse_version_output(output)


def get_version_from_sources(base):
    """Return the version declared in ``.osh/odoo/odoo/release.py``, or None."""
    release_file = base / ".osh" / "odoo" / "odoo" / "release.py"
    if not release_file.is_file():
        return None

    text = release_file.read_text()

    # Real Odoo release.py computes `version` from `version_info`.  Execute it
    # with a minimal builtins mapping so the computed value is available.
    namespace = {"__builtins__": {"str": str}}
    try:
        exec(text, namespace)  # noqa: S102
    except Exception as exc:
        echo.internal(f"Could not execute {release_file}: {exc}", err=True)
    else:
        version = namespace.get("version")
        if version is not None:
            return str(version)

    # Fallback for release files that simply set `version = "..."`.
    match = re.search(
        r'^version\s*=\s*(["\'])([^"\']+)\1\s*(?:#.*)?$',
        text,
        re.MULTILINE,
    )
    if match:
        return match.group(2)
    return None


def parse_version_output(text):
    """Return the first non-empty line from *text*, or None."""
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line
    return None
