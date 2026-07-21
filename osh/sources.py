"""Core helpers for acquiring Odoo source copies.

These helpers are shared by the local and Docker backends so that both can
resolve, cache and install Odoo, Enterprise and design-themes sources under
``.osh/``.
"""

import fnmatch
import os
import shutil
import subprocess
import sys
from pathlib import Path

import click

from . import echo
from .commons import run_subprocess
from .echo import confirm

DEFAULT_ODOO_URL = "https://github.com/odoo/odoo.git"
DEFAULT_ENTERPRISE_URL = "git@github.com:odoo/enterprise.git"
DEFAULT_THEMES_URL = "https://github.com/odoo/design-themes.git"
SOURCE_CACHE_DIR = Path.home() / ".cache" / "osh"


def ensure_osh_sources(
    base,
    version,
    edition,
    *,
    dry_run=False,
    skip_odoo=False,
    assume_yes=False,
    odoo_source=None,
    enterprise_source=None,
    themes_source=None,
):
    """Resolve, plan and install the required Odoo source copies.

    Sources are installed as links under ``base / ".osh"``.  The returned
    dictionary has the keys ``"odoo"``, ``"enterprise"`` and
    ``"design-themes"``; missing or skipped sources map to ``None``.
    """
    osh_dir = base / ".osh"

    include_enterprise = edition in ("ee", "sh") or enterprise_source is not None
    include_themes = edition == "sh" or themes_source is not None

    source_defs = []
    if not skip_odoo:
        source_defs.append(
            ("odoo", odoo_source, ("",), ("odoo-bin",), DEFAULT_ODOO_URL)
        )
    if include_enterprise:
        source_defs.append(
            (
                "enterprise",
                enterprise_source,
                ("enterprise", "*enterprise*"),
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

    if not source_defs:
        return {"odoo": None, "enterprise": None, "design-themes": None}

    source_plans = {}
    for name, flag, names, files, url in source_defs:
        project_source, requires_confirmation = _find_local_source(base, names, files)
        if requires_confirmation and not assume_yes and echo:
            if not confirm(f"Use {project_source} for {name}?", default=True):
                # User declined, fall back to default URL
                project_source = None
        source_plans[name] = SourceResolver(
            name, version, flag, project_source, osh_dir, url
        ).resolve()

    _display_source_plan(osh_dir, edition, source_plans)

    if dry_run:
        echo.info("Dry run: no changes made.", err=True)
        return {
            "odoo": None,
            "enterprise": None,
            "design-themes": None,
        }

    _confirm_sources(assume_yes)

    osh_dir.mkdir(parents=True, exist_ok=True)
    sources = {}
    for name, (action, spec, _warning) in source_plans.items():
        sources[name] = _install_source_plan(name, version, action, spec, osh_dir)

    for key in ("odoo", "enterprise", "design-themes"):
        sources.setdefault(key, None)
    return sources


def _display_source_plan(
    osh_dir,
    edition,
    source_plans,
):
    """Print the planned source-install actions for review."""
    echo.info(
        f"Will install Odoo sources under {osh_dir} for edition '{edition}':",
        err=True,
    )
    verbs = {
        "existing": "use existing",
        "replace": "replace with",
        "symlink": "symlink from",
        "clone": "clone from",
        "cache": "clone from central cache",
    }
    for name, (action, spec, warning) in source_plans.items():
        line = f"  {name}: {verbs[action]} {spec}"
        if warning:
            line += f"  [warning: {warning}]"
        echo.info(line, err=True)


def _confirm_sources(assume_yes):
    """Ask the user to confirm the source plan, or proceed automatically."""
    if assume_yes:
        echo.info("Proceeding with --yes (skipping confirmation).", err=True)
        return
    if sys.stdin.isatty():
        confirm("Proceed?", default=True, abort=True)
    else:
        echo.info("Proceeding in non-interactive mode.", err=True)


def _resolve_source(
    name,
    version,
    source_flag,
    project_source,
    osh_dir,
    default_url,
):
    """Return the planned action, source spec and optional warning for *name*."""
    return SourceResolver(
        name, version, source_flag, project_source, osh_dir, default_url
    ).resolve()


class SourceResolver:
    """Plan how to install a single Odoo source copy."""

    def __init__(
        self, name, version, source_flag, project_source, osh_dir, default_url
    ):
        self.name = name
        self.version = version
        self.source_flag = source_flag
        self.project_source = (
            project_source[0]
            if project_source and isinstance(project_source, tuple)
            else project_source
        )
        self.osh_dir = osh_dir
        self.default_url = default_url

    def resolve(self):
        """Return the planned action, source spec and an optional mismatch warning."""
        link = self.osh_dir / self.name
        if link.exists() or link.is_symlink():
            return self._resolve_existing(link)
        if self.source_flag:
            return self._resolve_flag()
        if self.project_source:
            return self._resolve_project()
        return self._resolve_cache()

    def _resolve_existing(self, link):
        resolved = link.resolve()
        warning = _source_branch_warning(resolved, self.version)

        # If the user supplied an explicit source, keep what they gave us and
        # only warn about a branch mismatch. Otherwise a managed source is
        # allowed to be replaced so ``osh init <new-version>`` can switch.
        if self.source_flag or self.project_source:
            return "existing", link, warning

        detected = _source_branch(resolved)
        if detected and not _version_matches(detected, self.version):
            return (
                "replace",
                self.default_url,
                f"on branch '{detected}', expected '{self.version}'",
            )
        return "existing", link, warning

    def _resolve_flag(self):
        local_path = Path(self.source_flag).expanduser().resolve()
        if not _is_git_url(self.source_flag) and local_path.is_dir():
            warning = _source_branch_warning(local_path, self.version)
            return "symlink", local_path, warning
        return "clone", self.source_flag, None

    def _resolve_project(self):
        warning = _source_branch_warning(self.project_source, self.version)
        return "symlink", self.project_source, warning

    def _resolve_cache(self):
        return "cache", self.default_url, None


def _install_source_plan(
    name,
    version,
    action,
    spec,
    osh_dir,
):
    """Execute a source plan entry and return the installed link, if any."""
    link = osh_dir / name
    if action == "existing":
        echo.info(f"Using existing {name} sources at {link}", err=True)
        return link

    if action == "replace":
        if link.is_symlink():
            link.unlink()
        elif link.is_dir():
            shutil.rmtree(link)
        elif link.exists():
            link.unlink()
        cache = _ensure_cache(name, version, str(spec))
        echo.info(
            f"Reinstalling {name} {version} from cache into {link} (shallow)\u2026",
            err=True,
        )
        _git_shallow_clone(f"file://{cache}", link, branch=version)
        return link

    if action == "symlink":
        echo.info(f"Linking {name} \u2192 {spec}\u2026", err=True)
        os.symlink(spec, link, target_is_directory=True)
        return link

    if action == "clone":
        echo.info(f"Cloning {name} from {spec} (shallow)\u2026", err=True)
        _git_shallow_clone(spec, link, branch=version)
        return link

    if action == "cache":
        cache = _ensure_cache(name, version, str(spec))
        echo.info(f"Cloning {name} from cache into {link} (shallow)\u2026", err=True)
        _git_shallow_clone(f"file://{cache}", link, branch=version)
        return link

    raise ValueError(f"Unknown source action: {action}")


def _ensure_cache(name, version, default_url):
    """Return the cached bare repo for *name*, creating or updating it as needed.

    The cache is populated with shallow fetches: only the requested version/branch
    is downloaded. Existing refs are never pruned.
    """
    cache = SOURCE_CACHE_DIR / f"{name}.git"
    if not cache.exists():
        SOURCE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        echo.info(f"Creating central {name} cache at {cache} (shallow)\u2026", err=True)
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
        echo.info(f"Fetching {name} {version} into cache\u2026", err=True)
        _fetch_refspec_into_cache(cache, name, version, default_url)
    return cache


def _fetch_refspec_into_cache(cache, name, version, default_url):
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


def _cache_has_branch(cache, version):
    """Return True if *cache* has a local ref for *version*."""
    for ref in (f"refs/heads/{version}", f"refs/tags/{version}"):
        returncode, _, _ = run_subprocess(
            ["git", "-C", str(cache), "show-ref", "--verify", "--quiet", ref],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if returncode == 0:
            return True
    return False


def _find_local_source(
    base,
    names,
    files,
):
    """Detect a local source directory inside *base*.

    *names* are candidate directory names (an empty string means *base* itself).
    *files* are glob patterns to look for inside the candidate directory.
    Returns a tuple of (path, requires_confirmation) where requires_confirmation is True
    if the source was found via a glob pattern match rather than exact name match.
    """

    # Check base directory itself
    for name in names:
        path = base / name if name else base
        if path.is_dir() and _has_manifest_file(path, files):
            return path.resolve(), False

    # Search recursively up to 9 levels deep for candidate directories
    # Only search subdirectories (not base itself, which was already checked)
    non_empty_names = [n for n in names if n]  # Filter out empty strings

    if non_empty_names:
        # Separate exact names from glob patterns
        exact_names = [n for n in non_empty_names if not n.startswith("*")]
        glob_patterns = [n for n in non_empty_names if n.startswith("*")]

        # Search for exact directory names first
        for cand in base.rglob("*"):
            # Limit depth to 9 levels
            if len(cand.relative_to(base).parts) > 9:
                continue
            if not cand.is_dir():
                continue
            for name in exact_names:
                if cand.name == name and _has_manifest_file(cand, files):
                    return cand.resolve(), False

        # Search for glob pattern matches (requires confirmation)
        if glob_patterns:
            for cand in base.rglob("*"):
                # Limit depth to 9 levels
                if len(cand.relative_to(base).parts) > 9:
                    continue
                if not cand.is_dir():
                    continue
                for pattern in glob_patterns:
                    # Pattern like "*enterprise*" should match directory names containing "enterprise"
                    if fnmatch.fnmatch(cand.name, pattern) and _has_manifest_file(
                        cand, files
                    ):
                        return cand.resolve(), True
    else:
        # If no specific names, search for any directory with manifest files
        for cand in base.rglob("*"):
            # Limit depth to 9 levels
            if len(cand.relative_to(base).parts) > 9:
                continue
            if not cand.is_dir():
                continue
            if _has_manifest_file(cand, files):
                return cand.resolve(), False
    return None, False


def _has_manifest_file(path, patterns):
    """Return True if *path* contains any file matching one of *patterns*."""
    return any(next(path.glob(pattern), None) is not None for pattern in patterns)


def _source_branch(path):
    """Return the git branch or tag for *path*, or None if not a git repo."""
    resolved = path.resolve()
    if not _is_git_repo(resolved):
        return None

    returncode, branch, _ = run_subprocess(
        ["git", "-C", str(resolved), "branch", "--show-current"],
        stderr=subprocess.DEVNULL,
    )
    if returncode == 0 and branch:
        return branch.strip()

    returncode, tag, _ = run_subprocess(
        ["git", "-C", str(resolved), "describe", "--tags", "--exact-match"],
        stderr=subprocess.DEVNULL,
    )
    if returncode == 0 and tag:
        return tag.strip()
    return None


def _is_git_repo(path):
    """Return True if *path* contains a ``.git`` directory (following symlinks)."""
    if (path / ".git").exists():
        return True
    return path.is_symlink() and (path.resolve() / ".git").exists()


def _version_matches(detected, version):
    """Return True if *detected* looks like it contains *version*."""
    if not detected:
        return True
    detected = detected.lower().replace("~", "-").replace("_", "-")
    version = version.lower().replace("~", "-").replace("_", "-")
    return version in detected


def _source_branch_warning(path, version):
    """Return a warning if *path* does not appear to match *version*."""
    detected = _source_branch(path)
    if detected and not _version_matches(detected, version):
        return f"on branch '{detected}', expected '{version}'"
    return None


def _is_git_url(path):
    """Return True if *path* appears to be a git URL."""
    return path.startswith(
        ("git@", "git://", "https://", "http://", "ssh://", "file://")
    )


def _git_shallow_clone(
    url,
    target,
    *,
    branch=None,
    depth=1,
    single_branch=True,
):
    """Clone a git repository with shallow history.

    Args:
        url: Git repository URL
        target: Target directory for the clone
        branch: Branch to clone (defaults to repository default)
        depth: Clone depth (default: 1 for shallow clone)
        single_branch: Clone only the specified branch (default: True)
    """
    cmd = ["git", "clone", "--progress", "--depth", str(depth)]
    if single_branch:
        cmd.append("--single-branch")
    if branch:
        cmd.extend(["--branch", branch])
    cmd.extend([url, str(target)])
    subprocess.check_call(cmd)
