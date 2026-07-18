"""Core helpers for acquiring Odoo source copies.

These helpers are shared by the local and Docker backends so that both can
resolve, cache and install Odoo, Enterprise and design-themes sources under
``.osh/``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import click

DEFAULT_ODOO_URL = "https://github.com/odoo/odoo.git"
DEFAULT_ENTERPRISE_URL = "git@github.com:odoo/enterprise.git"
DEFAULT_THEMES_URL = "https://github.com/odoo/design-themes.git"
SOURCE_CACHE_DIR = Path.home() / ".cache" / "osh"


def ensure_osh_sources(
    base: Path,
    version: str,
    edition: str,
    *,
    dry_run: bool = False,
    skip_odoo: bool = False,
    assume_yes: bool = False,
    odoo_source: str | None = None,
    enterprise_source: str | None = None,
    themes_source: str | None = None,
) -> dict[str, Path | None]:
    """Resolve, plan and install the required Odoo source copies.

    Sources are installed as links under ``base / ".osh"``.  The returned
    dictionary has the keys ``"odoo"``, ``"enterprise"`` and
    ``"design-themes"``; missing or skipped sources map to ``None``.
    """
    osh_dir = base / ".osh"

    include_enterprise = edition in ("ee", "sh") or enterprise_source is not None
    include_themes = edition == "sh" or themes_source is not None

    source_defs: list[tuple[str, str | None, tuple[str, ...], tuple[str, ...], str]] = (
        []
    )
    if not skip_odoo:
        source_defs.append(
            ("odoo", odoo_source, ("",), ("odoo-bin",), DEFAULT_ODOO_URL)
        )
    if include_enterprise:
        source_defs.append(
            (
                "enterprise",
                enterprise_source,
                ("enterprise",),
                ("*/__manifest__.py", "*/__openerp__.py"),
                DEFAULT_ENTERPRISE_URL,
            )
        )
    if include_themes:
        source_defs.append(
            (
                "design-themes",
                themes_source,
                ("design-themes", "themes"),
                ("*/__manifest__.py", "*/__openerp__.py"),
                DEFAULT_THEMES_URL,
            )
        )

    source_plans = {
        name: _resolve_source(
            name, version, flag, _find_local_source(base, names, files), osh_dir, url
        )
        for name, flag, names, files, url in source_defs
    }

    _display_source_plan(osh_dir, edition, source_plans)

    if dry_run:
        click.echo("Dry run: no changes made.", err=True)
        return {
            "odoo": None,
            "enterprise": None,
            "design-themes": None,
        }

    _confirm_sources(assume_yes)

    osh_dir.mkdir(parents=True, exist_ok=True)
    sources: dict[str, Path | None] = {}
    for name, (action, spec, _warning) in source_plans.items():
        sources[name] = _install_source_plan(name, version, action, spec, osh_dir)

    for key in ("odoo", "enterprise", "design-themes"):
        sources.setdefault(key, None)
    return sources


def _display_source_plan(
    osh_dir: Path,
    edition: str,
    source_plans: dict[str, tuple[str, str | Path, str | None]],
) -> None:
    """Print the planned source-install actions for review."""
    click.echo(
        f"Will install Odoo sources under {osh_dir} for edition '{edition}':",
        err=True,
    )
    verbs = {
        "existing": "use existing",
        "symlink": "symlink from",
        "clone": "clone from",
        "cache": "clone from central cache",
    }
    for name, (action, spec, warning) in source_plans.items():
        line = f"  {name}: {verbs[action]} {spec}"
        if warning:
            line += f"  [warning: {warning}]"
        click.echo(line, err=True)


def _confirm_sources(assume_yes: bool) -> None:
    """Ask the user to confirm the source plan, or proceed automatically."""
    if assume_yes:
        click.echo("Proceeding with --yes (skipping confirmation).", err=True)
        return
    if sys.stdin.isatty():
        click.confirm("Proceed?", default=True, abort=True)
    else:
        click.echo("Proceeding in non-interactive mode.", err=True)


def _resolve_source(
    name: str,
    version: str,
    source_flag: str | None,
    project_source: Path | None,
    osh_dir: Path,
    default_url: str,
) -> tuple[str, str | Path, str | None]:
    """Return the planned action, source spec and an optional mismatch warning."""
    link = osh_dir / name
    if link.exists() or link.is_symlink():
        warning = _source_branch_warning(link.resolve(), version)
        return "existing", link, warning

    if source_flag:
        local_path = Path(source_flag).expanduser().resolve()
        if not _is_git_url(source_flag) and local_path.is_dir():
            warning = _source_branch_warning(local_path, version)
            return "symlink", local_path, warning
        return "clone", source_flag, None

    if project_source:
        warning = _source_branch_warning(project_source, version)
        return "symlink", project_source, warning

    return "cache", default_url, None


def _install_source_plan(
    name: str,
    version: str,
    action: str,
    spec: str | Path,
    osh_dir: Path,
) -> Path | None:
    """Execute a source plan entry and return the installed link, if any."""
    link = osh_dir / name
    if action == "existing":
        click.echo(f"Using existing {name} sources at {link}", err=True)
        return link

    if action == "symlink":
        click.echo(f"Linking {name} \u2192 {spec}\u2026", err=True)
        os.symlink(spec, link, target_is_directory=True)
        return link

    if action == "clone":
        click.echo(f"Cloning {name} from {spec} (shallow)\u2026", err=True)
        _git_shallow_clone(spec, link, branch=version)
        return link

    if action == "cache":
        cache = _ensure_cache(name, version, str(spec))
        click.echo(f"Cloning {name} from cache into {link} (shallow)\u2026", err=True)
        _git_shallow_clone(f"file://{cache}", link, branch=version)
        return link

    raise ValueError(f"Unknown source action: {action}")


def _ensure_cache(name: str, version: str, default_url: str) -> Path:
    """Return the cached bare repo for *name*, creating or updating it as needed.

    The cache is populated with shallow fetches: only the requested version/branch
    is downloaded. Existing refs are never pruned.
    """
    cache = SOURCE_CACHE_DIR / f"{name}.git"
    if not cache.exists():
        SOURCE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        click.echo(
            f"Creating central {name} cache at {cache} (shallow)\u2026", err=True
        )
        subprocess.check_call(
            [
                "git",
                "clone",
                "--progress",
                "--bare",
                "--depth",
                "1",
                "--branch",
                version,
                default_url,
                str(cache),
            ]
        )
    if not _cache_has_branch(cache, version):
        click.echo(f"Fetching {name} {version} into cache\u2026", err=True)
        _fetch_refspec_into_cache(cache, name, version, default_url)
    return cache


def _fetch_refspec_into_cache(
    cache: Path, name: str, version: str, default_url: str
) -> None:
    """Shallow-fetch *version* into *cache*, trying branch then tag refspecs."""
    refspecs = [
        f"refs/heads/{version}:refs/heads/{version}",
        f"refs/tags/{version}:refs/tags/{version}",
    ]
    for refspec in refspecs:
        try:
            subprocess.check_call(
                [
                    "git",
                    "-C",
                    str(cache),
                    "fetch",
                    "--progress",
                    "--depth",
                    "1",
                    "origin",
                    refspec,
                ]
            )
            return
        except subprocess.CalledProcessError as exc:
            if refspec == refspecs[-1]:
                raise click.ClickException(
                    f"Could not fetch {name} {version} from {default_url}"
                ) from exc


def _cache_has_branch(cache: Path, version: str) -> bool:
    """Return True if *cache* has a local ref for *version*."""
    for ref in (f"refs/heads/{version}", f"refs/tags/{version}"):
        res = subprocess.run(
            ["git", "-C", str(cache), "show-ref", "--verify", "--quiet", ref],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if res.returncode == 0:
            return True
    return False


def _find_local_source(
    base: Path,
    names: tuple[str, ...],
    files: tuple[str, ...],
) -> Path | None:
    """Detect a local source directory inside *base*.

    *names* are candidate directory names (an empty string means *base* itself).
    *files* are glob patterns to look for inside the candidate directory.
    """
    candidates = [base] + [p for p in base.iterdir() if p.is_dir()]
    for cand in candidates:
        for name in names:
            path = cand / name if name else cand
            if path.is_dir() and _has_manifest_file(path, files):
                return path.resolve()
    return None


def _has_manifest_file(path: Path, patterns: tuple[str, ...]) -> bool:
    """Return True if *path* contains any file matching one of *patterns*."""
    return any(next(path.glob(pattern), None) is not None for pattern in patterns)


def _source_branch(path: Path) -> str | None:
    """Return the git branch or tag for *path*, or None if not a git repo."""
    resolved = path.resolve()
    if not _is_git_repo(resolved):
        return None
    try:
        branch = subprocess.check_output(
            ["git", "-C", str(resolved), "branch", "--show-current"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if branch:
            return branch
        tag = subprocess.check_output(
            ["git", "-C", str(resolved), "describe", "--tags", "--exact-match"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if tag:
            return tag
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return None


def _is_git_repo(path: Path) -> bool:
    """Return True if *path* contains a ``.git`` directory (following symlinks)."""
    if (path / ".git").exists():
        return True
    return path.is_symlink() and (path.resolve() / ".git").exists()


def _version_matches(detected: str | None, version: str) -> bool:
    """Return True if *detected* looks like it contains *version*."""
    if not detected:
        return True
    detected = detected.lower().replace("~", "-").replace("_", "-")
    version = version.lower().replace("~", "-").replace("_", "-")
    return version in detected


def _source_branch_warning(path: Path, version: str) -> str | None:
    """Return a warning if *path* does not appear to match *version*."""
    detected = _source_branch(path)
    if detected and not _version_matches(detected, version):
        return f"on branch '{detected}', expected '{version}'"
    return None


def _is_git_url(path: str) -> bool:
    """Return True if *path* appears to be a git URL."""
    return path.startswith(
        ("git@", "git://", "https://", "http://", "ssh://", "file://")
    )


def _git_shallow_clone(
    url: str,
    target: Path,
    *,
    branch: str | None = None,
    depth: int = 1,
    single_branch: bool = True,
) -> None:
    """Clone a git repository with shallow history.

    Args:
        url: Git repository URL
        target: Target directory for the clone
        branch: Branch to clone (defaults to repository default)
        depth: Clone depth (default: 1 for shallow clone)
        single_branch: Clone only the specified branch (default: True)
    """
    cmd = ["git", "clone", "--depth", str(depth)]
    if single_branch:
        cmd.append("--single-branch")
    if branch:
        cmd.extend(["--branch", branch])
    cmd.extend([url, str(target)])
    subprocess.check_call(cmd)
