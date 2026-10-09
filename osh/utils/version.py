"""Centralized Odoo version detection helpers."""

import re
from collections import Counter
from pathlib import Path

from .. import echo
from ..common import run_subprocess


def detect_project_version(base):
    """Best-effort Odoo version for *base*, as ``(version, source)``.

    Heuristics, most trustworthy first: checked-out Odoo sources
    (``odoo/release.py``), git-aggregator ``repos.yml`` refs, addon
    manifest version pins, the checked-out git branch name. Returns
    ``(None, None)`` when nothing gives a hint.
    """
    for release_file in _project_release_files(base):
        if version := read_release_version(release_file):
            return version, str(release_file.relative_to(base))
    for finder, source in (
        (_version_from_repos_yml, "repos.yml"),
        (_version_from_manifests, "an addon manifest"),
        (_version_from_branch, "the git branch"),
    ):
        if version := finder(base):
            return version, source
    return None, None


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


def version_major(version):
    """Return the major Odoo version number in *version*, or None.

    Accepts ``"19.0"`` and ``"saas-19.4"``-style strings.
    """
    match = re.match(r"(?:saas[-~])?(\d+)", str(version or "").strip())
    return int(match.group(1)) if match else None


def get_version_from_sources(base):
    """Return the version declared in ``.osh/odoo/odoo/release.py``, or None."""
    return read_release_version(base / ".osh" / "odoo" / "odoo" / "release.py")


def read_release_version(release_file):
    """Return the ``version`` an Odoo ``release.py`` defines, or None."""
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


# ``osh init`` project heuristics --------------------------------------------

_REF_VERSION = re.compile(r"saas-\d+\.\d+|\d+\.\d+")

_MANIFEST_VERSION = re.compile(r"""['"]version['"]\s*:\s*['"](?:saas-)?(\d+\.\d+)""")


def _project_release_files(base):
    """Candidate ``odoo/release.py`` paths of source checkouts in *base*."""
    seen = set()
    for pattern in ("*/odoo/release.py", "*/*/odoo/release.py"):
        for path in sorted(base.glob(pattern)):
            if path not in seen:
                seen.add(path)
                yield path


def _version_from_repos_yml(base):
    """Modal Odoo version among git-aggregator refs in ``repos.yml``."""
    repos = base / "repos.yml"
    if not repos.is_file():
        return None
    counts = Counter(_REF_VERSION.findall(repos.read_text()))
    ranked = counts.most_common(2)
    if ranked and (len(ranked) == 1 or ranked[0][1] > ranked[1][1]):
        return ranked[0][0]
    return None


def _version_from_manifests(base):
    """Version pin of the first addon manifest found, e.g. ``19.0.1.0``."""
    for pattern in ("*/__manifest__.py", "*/*/__manifest__.py"):
        for manifest in sorted(base.glob(pattern)):
            match = _MANIFEST_VERSION.search(manifest.read_text())
            if match:
                return match.group(1)
    return None


def _version_from_branch(base):
    """Odoo version embedded in the checked-out branch name, or None.

    ``staging-19`` and ``19.0-mig-x`` read as ``19.0``; ``saas-19.4`` keeps
    its saas form. Only two-digit majors qualify — ``release/1.11.0``-style
    names are not Odoo versions.
    """
    branch = _current_branch(base)
    if not branch:
        return None
    if match := re.search(r"saas-\d+\.\d+", branch):
        return match.group(0)
    if match := re.search(r"(?<![\d.])(\d{2})\.(\d+)(?![\d.])", branch):
        return match.group(0)
    if match := re.search(r"(?<![\d.])(\d{2})(?![\d.])", branch):
        return f"{match.group(1)}.0"
    return None


def _current_branch(base):
    """Checked-out branch from ``.git/HEAD``; None when detached or unknown."""
    git_dir = base / ".git"
    if git_dir.is_file():
        # Worktrees and submodules keep a 'gitdir: <path>' pointer file.
        text = git_dir.read_text(encoding="utf-8").strip()
        if not text.startswith("gitdir:"):
            return None
        git_dir = Path(text.split(":", 1)[1].strip())
        if not git_dir.is_absolute():
            git_dir = (base / git_dir).resolve()
    head = git_dir / "HEAD"
    if not head.is_file():
        return None
    ref = head.read_text(encoding="utf-8").strip()
    prefix = "ref: refs/heads/"
    return ref[len(prefix) :] if ref.startswith(prefix) else None
