"""Centralized Odoo version detection helpers."""

import re

from .. import echo
from ..common import run_subprocess


def get_version_from_executable(exe):
    """Return the version reported by an Odoo executable, or None."""
    try:
        returncode, stdout, stderr = run_subprocess([str(exe), "--version"])
    except (OSError, ValueError):
        return None

    output = (stdout or stderr or "").strip()
    if returncode != 0 or not output:
        return None
    return parse_version_output(output)


def get_version_tuple(exe):
    """Return the installed Odoo version as a (major, minor) tuple, or None."""
    output = get_version_from_executable(exe)
    if not output:
        return None
    match = re.search(r"(\d+)\.(\d+)", output)
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)))


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
    except (SyntaxError, NameError, ValueError, TypeError, AttributeError) as exc:
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
