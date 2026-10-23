"""Core helpers for acquiring Odoo source copies.

These helpers are shared by the local and Docker runtimes so that both can
resolve, cache and install Odoo, Enterprise and design-themes sources under
``.osh/``.
"""

import configparser
import fnmatch
import os
import shutil
import sys
from pathlib import Path

import click

from . import echo
from .common import env_root, run_subprocess

DEFAULT_ODOO_URL = "https://github.com/odoo/odoo.git"
DEFAULT_ENTERPRISE_URL = "git@github.com:odoo/enterprise.git"
DEFAULT_THEMES_URL = "https://github.com/odoo/design-themes.git"
SOURCE_CACHE_DIR = Path.home() / ".cache" / "osh"
SOURCE_NAMES = ("odoo", "enterprise", "design-themes")


def ensure_osh_sources(
    base,
    version,
    edition,
    *,
    dry_run=False,
    skip_odoo=False,
    assume_yes=False,
    confirmed=False,
    odoo_source=None,
    enterprise_source=None,
    themes_source=None,
    pins=None,
):
    """Resolve, plan and install the required Odoo source copies.

    Sources are installed as links under ``base / ".osh"``.  The returned
    dictionary has the keys ``"odoo"``, ``"enterprise"`` and
    ``"design-themes"``; missing or skipped sources map to ``None``.

    *confirmed* marks a higher-level plan the user already approved: it
    skips only the redundant plan confirmation below, whereas *assume_yes*
    silences every prompt — including the submodule guard, which asks a
    question the user could not have seen when approving the plan.
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

    _warn_uninitialized_submodules(base, source_defs, assume_yes=assume_yes)

    source_plans = {}
    for name, flag, names, files, url in source_defs:
        project_source, requires_confirmation = _find_local_source(base, names, files)
        if requires_confirmation and not assume_yes and echo:
            if not echo.confirm(f"Use {project_source} for {name}?", default=True):
                # User declined, fall back to default URL
                project_source = None
        source_plans[name] = _resolve_source(
            name, version, flag, project_source, osh_dir, url
        )

    _display_source_plan(osh_dir, edition, source_plans)

    if dry_run:
        echo.info("Dry run: no changes made.", err=True)
        return {
            "odoo": None,
            "enterprise": None,
            "design-themes": None,
        }

    _confirm_sources(assume_yes, confirmed)

    osh_dir.mkdir(parents=True, exist_ok=True)
    sources = {}
    for name, (action, spec, _warning) in source_plans.items():
        sources[name] = _install_source_plan(name, version, action, spec, osh_dir)
        if pins and pins.get(name) and sources[name] is not None:
            _apply_source_pin(sources[name], name, pins[name], version, dry_run=dry_run)

    for key in SOURCE_NAMES:
        sources.setdefault(key, None)
    return sources


def _warn_uninitialized_submodules(base, source_defs, assume_yes=False):
    """Warn when required sources are configured as uninitialized git submodules."""
    gitmodules = base / ".gitmodules"
    if not gitmodules.is_file() or not _is_git_repo(base):
        return

    submodule_info = _read_gitmodules(base)
    if not submodule_info:
        return

    returncode, stdout, _ = run_subprocess(
        ["git", "-C", str(base), "submodule", "status"]
    )
    if returncode != 0:
        return

    uninitialized = set()
    for line in stdout.splitlines():
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith("-"):
            uninitialized.add(parts[1])

    needed_names = {name for name, flag, *_ in source_defs if flag is None}
    matches = []
    for path, url in submodule_info.items():
        if path not in uninitialized:
            continue
        source_name = _submodule_source_name(path, url)
        if source_name and source_name in needed_names:
            matches.append((path, source_name))

    if not matches:
        return

    details = ", ".join(f"{path} ({source_name})" for path, source_name in matches)
    paths = " ".join(path for path, _ in matches)
    echo.warning(
        f"Uninitialized git submodules detected: {details}. "
        f"Run 'git submodule update --init --recursive {paths}' to use them, "
        "or Osh will fetch fresh copies instead."
    )
    if not assume_yes and sys.stdin.isatty():
        if not echo.confirm("Continue and fetch fresh copies instead?", default=False):
            raise click.ClickException(
                "Aborted. Initialize the submodules and run 'osh init' again."
            )


def _read_gitmodules(base):
    """Return a mapping of {path: url} for submodules in *base/.gitmodules*."""
    gitmodules = base / ".gitmodules"
    try:
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read(gitmodules, encoding="utf-8")
    except (OSError, configparser.Error):
        return {}

    result = {}
    for section in cfg.sections():
        if not section.startswith("submodule "):
            continue
        path = cfg.get(section, "path", fallback=None)
        url = cfg.get(section, "url", fallback=None)
        if path:
            result[path] = url
    return result


def _submodule_source_name(path, url=None):
    """Guess whether a submodule path is for odoo, enterprise or design-themes."""
    names = [_repo_name_from_url(url)] if url else []
    names.append(str(path).lower().rstrip("/").split("/")[-1])
    for name in names:
        if name in ("odoo", "enterprise"):
            return name
        if name in ("design-themes", "design_themes", "themes"):
            return "design-themes"
    return None


def _repo_name_from_url(url):
    """Return the repository name from a git URL or path."""
    if not url:
        return None
    url = url.rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    for sep in ("/", ":"):
        if sep in url:
            return url.split(sep)[-1].lower()
    return url.lower()


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


def _confirm_sources(assume_yes, confirmed=False):
    """Ask the user to confirm the source plan, or proceed automatically."""
    if assume_yes:
        echo.info("Proceeding with --yes (skipping confirmation).", err=True)
        return
    if confirmed:
        return
    if sys.stdin.isatty():
        echo.confirm("Proceed?", default=True, abort=True)
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
    if project_source and isinstance(project_source, tuple):
        project_source = project_source[0]

    link = osh_dir / name
    if link.exists() or link.is_symlink():
        return _resolve_existing(
            link, version, source_flag, project_source, default_url
        )
    if source_flag:
        return _resolve_flag(source_flag, version)
    if project_source:
        return _resolve_project(project_source, version)
    return _resolve_cache(default_url)


def _resolve_existing(link, version, source_flag, project_source, default_url):
    """Keep or replace a source link that already exists."""
    resolved = link.resolve()
    warning = _source_branch_warning(resolved, version)

    # If the user supplied an explicit source, keep what they gave us and
    # only warn about a branch mismatch. Otherwise a managed source is
    # allowed to be replaced so ``osh init <new-version>`` can switch.
    if source_flag or project_source:
        return "existing", link, warning

    detected = _source_branch(resolved)
    if detected and not _version_matches(detected, version):
        return (
            "replace",
            default_url,
            f"on branch '{detected}', expected '{version}'",
        )
    return "existing", link, warning


def _resolve_flag(source_flag, version):
    """Resolve a source supplied via --*-source CLI option."""
    local_path = Path(source_flag).expanduser().resolve()
    if not _is_git_url(source_flag) and local_path.is_dir():
        warning = _source_branch_warning(local_path, version)
        return "symlink", local_path, warning
    return "clone", source_flag, None


def _resolve_project(project_source, version):
    """Resolve a source discovered in the project directory."""
    warning = _source_branch_warning(project_source, version)
    return "symlink", project_source, warning


def _resolve_cache(default_url):
    """Resolve a source by using the shared cache."""
    return "cache", default_url, None


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
        cache = _ensure_repo_cache(name, version, str(spec))
        echo.info(
            f"Reinstalling {name} {version} from cache into {link} (shallow)\u2026",
            err=True,
        )
        _git_shallow_clone(f"file://{cache}", link, branch=version)
        return link

    if action == "symlink":
        echo.info(f"Linking {name} \u2192 {spec}\u2026", err=True)
        # Prefer a relative link for targets under the project root so it
        # survives host path moves and container mounts (e.g. the Docker
        # runtime's /mnt/extra-addons).
        try:
            Path(spec).resolve().relative_to(osh_dir.parent.resolve())
            target = os.path.relpath(spec, link.parent)
        except (ValueError, OSError):
            target = str(spec)
        os.symlink(target, link, target_is_directory=True)
        return link

    if action == "clone":
        echo.info(f"Cloning {name} from {spec} (shallow)\u2026", err=True)
        _git_shallow_clone(spec, link, branch=version)
        return link

    if action == "cache":
        cache = _ensure_repo_cache(name, version, str(spec))
        echo.info(f"Cloning {name} from cache into {link} (shallow)\u2026", err=True)
        _git_shallow_clone(f"file://{cache}", link, branch=version)
        return link

    raise ValueError(f"Unknown source action: {action}")


def _ensure_repo_cache(name, version, default_url):
    """Return the cached bare repo for *name*, creating or updating it as needed.

    The cache is populated with shallow fetches: only the requested version/branch
    is downloaded. Existing refs are never pruned.
    """
    cache = SOURCE_CACHE_DIR / f"{name}.git"
    if not cache.exists():
        SOURCE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        echo.info(f"Creating central {name} cache at {cache} (shallow)\u2026", err=True)
        returncode, _, _ = run_subprocess(
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
        if returncode != 0:
            raise click.ClickException(
                f"Could not clone {name} {version} from {default_url}"
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
        returncode, _, _ = run_subprocess(
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
        if returncode == 0:
            return
    raise click.ClickException(f"Could not fetch {name} {version} from {default_url}")


def _cache_has_branch(cache, version):
    """Return True if *cache* has a local ref for *version*."""
    for ref in (f"refs/heads/{version}", f"refs/tags/{version}"):
        returncode, _, _ = run_subprocess(
            ["git", "-C", str(cache), "show-ref", "--verify", "--quiet", ref]
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
        ["git", "-C", str(resolved), "branch", "--show-current"]
    )
    if returncode == 0 and branch:
        return branch.strip()

    returncode, tag, _ = run_subprocess(
        ["git", "-C", str(resolved), "describe", "--tags", "--exact-match"]
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
    returncode, _, _ = run_subprocess(cmd)
    if returncode != 0:
        raise click.ClickException(f"Could not clone {url} into {target}")


def parse_source_pins(refs, dates, source_names=SOURCE_NAMES):
    """Parse ``--ref``/``--date`` values into ``{name: (kind, value)}``.

    *kind* is ``"rev"`` (branch or tag name), ``"sha"`` (a full 40-hex
    commit id) or ``"date"`` (an approxidate resolving to the newest
    commit on or before it). A ``NAME=`` prefix scopes the pin to one
    source; a bare value applies to all of them.
    """
    pins = {}
    for raw in refs:
        _add_source_pin(pins, raw, _pin_kind_for_ref, source_names)
    for raw in dates:
        _add_source_pin(pins, raw, lambda _value: "date", source_names)
    return pins


def update_osh_sources(
    base=None, *, version=None, pins=None, dry_run=False, force=False
):
    """Refresh managed Odoo sources: central cache refs and project clones.

    In a project (*base* given) each managed ``.osh/<name>`` clone is
    fetched and switched to its upstream tip — or to the pin's target
    when *pins* has one. Linked sources and dirty checkouts (without
    *force*) are reported and skipped. Without *base*, every branch ref
    cached under ``SOURCE_CACHE_DIR`` is refreshed.
    """
    if base is None:
        _update_source_caches(dry_run=dry_run)
        if pins:
            echo.warning("Source pins are ignored outside a project.", err=True)
        return
    osh_dir = env_root(base) / ".osh"
    found = False
    for name in SOURCE_NAMES:
        link = osh_dir / name
        if not link.exists() and not link.is_symlink():
            continue
        found = True
        pin = (pins or {}).get(name)
        if link.is_symlink():
            message = (
                f"{name}: linked source at {link.resolve()} — "
                "update it in its own checkout; skipped."
            )
            if pin:
                echo.warning(f"{message} Pin ignored.", err=True)
            else:
                echo.info(message, err=True)
            continue
        if not _is_git_repo(link):
            echo.warning(f"{name}: {link} is not a git checkout — skipped.", err=True)
            continue
        _update_source_clone(link, name, version, pin, dry_run=dry_run, force=force)
    if not found:
        echo.info(f"No managed source clones under {osh_dir}.", err=True)


def _add_source_pin(pins, raw, kind_for, source_names):
    """Add one ``[NAME=]VALUE`` pin to *pins*, validating its scope."""
    name, sep, value = raw.partition("=")
    if not sep:
        name, value = None, raw
    elif name not in source_names:
        raise click.ClickException(
            f"Unknown source '{name}' in pin '{raw}'. "
            f"Expected one of: {', '.join(source_names)}."
        )
    value = value.strip()
    if not value:
        raise click.ClickException(f"Empty pin value in '{raw}'.")
    kind = kind_for(value)
    for target in [name] if name else source_names:
        if target in pins:
            raise click.ClickException(f"Conflicting pins for source '{target}'.")
        pins[target] = (kind, value)


def _pin_kind_for_ref(value):
    """Classify a ``--ref`` value: a full 40-hex id is a commit sha."""
    if len(value) == 40 and all(c in "0123456789abcdefABCDEF" for c in value):
        return "sha"
    return "rev"


def _update_source_caches(*, dry_run=False):
    """Force-fetch every branch ref cached under ``SOURCE_CACHE_DIR``."""
    if not SOURCE_CACHE_DIR.is_dir():
        echo.info(
            f"No source cache at {SOURCE_CACHE_DIR} yet — nothing to update.",
            err=True,
        )
        return
    for cache in sorted(SOURCE_CACHE_DIR.glob("*.git")):
        name = cache.name[: -len(".git")]
        returncode, out, _ = run_subprocess(
            [
                "git",
                "-C",
                str(cache),
                "for-each-ref",
                "--format=%(refname)",
                "refs/heads",
            ]
        )
        refs = [line.strip() for line in out.splitlines() if line.strip()]
        if not refs:
            echo.info(f"{name}: no cached branches.", err=True)
            continue
        for ref in refs:
            branch = ref.split("/", 2)[-1]
            old = _git_rev(cache, ref)
            returncode, _, stderr = run_subprocess(
                [
                    "git",
                    "-C",
                    str(cache),
                    "fetch",
                    "--depth",
                    "1",
                    "origin",
                    f"+{ref}:{ref}",
                ],
                dry_run=dry_run,
            )
            if dry_run or returncode is None:
                continue
            if returncode != 0:
                echo.warning(
                    f"{name}: could not fetch '{branch}': "
                    f"{stderr.strip().splitlines()[-1] if stderr.strip() else 'failed'}",
                    err=True,
                )
                continue
            new = _git_rev(cache, ref)
            if new == old:
                echo.info(
                    f"{name}: {branch} already up to date ({_short(old)}).",
                    err=True,
                )
            else:
                echo.info(
                    f"{name}: {branch} {_short(old)} -> {_short(new)}",
                    err=True,
                )


def _update_source_clone(link, name, version, pin, *, dry_run, force):
    """Fetch and reset the managed source clone at *link*.

    *pin* is a ``(kind, value)`` pair or None — without one the clone is
    updated to its checked-out branch's tip (the recorded *version*
    branch when the checkout is detached).
    """
    resolved = link.resolve()
    branch = _source_branch(resolved) or version
    kind, value = pin or ("rev", branch)
    if not value:
        echo.warning(
            f"{name}: detached checkout and no recorded version — skipped.",
            err=True,
        )
        return
    if _git_dirty(resolved) and not force:
        echo.warning(
            f"{name}: uncommitted changes in {resolved} — skipped "
            "(use --force to discard them).",
            err=True,
        )
        return
    repo = _resolving_repo(resolved, name)
    target = _resolve_target(
        repo, kind, value, branch, track_ref=repo != resolved, dry_run=dry_run
    )
    if target is None:
        return
    sha, mode, branch_name = target
    old_sha = _git_rev(resolved, "HEAD")
    current_branch = _source_branch(resolved)
    already = (
        sha == old_sha
        and (mode == "detach") == (current_branch is None)
        and (mode != "branch" or current_branch == branch_name)
    )
    if already:
        echo.info(f"{name}: already up to date ({_short(sha)}).", err=True)
        return
    _checkout_target(resolved, sha, mode, branch_name, force=force)
    label = f" on '{branch_name}'" if mode == "branch" else " (pinned)"
    echo.info(f"{name}: {_short(old_sha)} -> {_short(sha)}{label}", err=True)


def _apply_source_pin(link, name, pin, version, *, dry_run=False):
    """Check out *pin* on the source installed at *link*.

    Linked sources are user-owned checkouts — the pin is reported as
    ignored. Managed clones go through the same update path as
    ``update_osh_sources`` (a fresh clone just has nothing to discard).
    """
    link = Path(link)
    if link.is_symlink():
        echo.warning(
            f"{name}: pin ignored — {link} links to {link.resolve()}; "
            "check out the pin in that checkout.",
            err=True,
        )
        return
    _update_source_clone(link, name, version, pin, dry_run=dry_run, force=False)


def _resolving_repo(resolved, name):
    """Return the repo to resolve pin targets in for the clone *resolved*.

    Clones whose origin is the central cache resolve against the cache —
    it talks to the real upstream; everything else resolves in place.
    """
    cache = SOURCE_CACHE_DIR / f"{name}.git"
    if not cache.is_dir():
        return resolved
    returncode, url, _ = run_subprocess(
        ["git", "-C", str(resolved), "remote", "get-url", "origin"]
    )
    if returncode == 0 and url.strip() == f"file://{cache}":
        return cache
    return resolved


def _resolve_target(repo, kind, value, branch, *, track_ref, dry_run=False):
    """Fetch the pin target into *repo*; return ``(sha, mode, name)``.

    *mode* is ``"branch"`` when *value* named a branch — the clone then
    switches to that branch — or ``"detach"`` for tags, shas and dates,
    which check out the commit detached. *track_ref* additionally moves
    the repo's own ``refs/heads/<branch>`` to the fetched tip; used for
    the central cache so its tracked ref stays fresh. Returns None under
    *dry_run* (fetches are only announced).
    """
    if dry_run:
        detail = {
            "rev": f"ref '{value}'",
            "sha": f"commit {value}",
            "date": f"newest commit on '{branch}' at or before {value}",
        }[kind]
        echo.info(f"Would fetch {detail} in {repo}", err=True)
        return None
    if kind == "sha":
        return _resolve_sha(repo, value, branch)
    if kind == "date":
        return _resolve_date(repo, value, branch, track_ref=track_ref)
    return _resolve_rev(repo, value, track_ref=track_ref)


def _resolve_sha(repo, value, branch):
    """Fetch a commit sha into *repo*; deepen the tracked branch as fallback.

    GitHub and ``file://`` transports serve unadvertised shas, but servers
    without upload-pack's ``allow*SHA1InWant`` refuse them — then the
    tracked branch's history is deepened until the commit appears.
    """
    returncode, _, _ = run_subprocess(
        ["git", "-C", str(repo), "fetch", "--depth", "1", "origin", value]
    )
    if returncode == 0:
        return value.lower(), "detach", None
    if branch and _deepen_until(
        repo,
        f"refs/heads/{branch}",
        lambda: _git_rev(repo, f"{value}^{{commit}}"),
    ):
        return value.lower(), "detach", None
    raise click.ClickException(
        f"Could not fetch commit {value} in {repo} — it may live on a "
        "different branch than the checkout's."
    )


def _resolve_date(repo, value, branch, *, track_ref):
    """Resolve 'newest commit at or before *value*' on *branch* in *repo*.

    ``--shallow-since`` errors out when no commit newer than the date
    exists (e.g. the branch tip predates it), so the tip is fetched
    plainly and the shallow history deepened until ``rev-list --before``
    resolves the as-of commit.
    """
    if not branch:
        raise click.ClickException(
            f"--date needs a branch to resolve against; "
            f"'{value}' was given for a detached or unknown checkout."
        )
    ref = f"refs/heads/{branch}"
    returncode, _, _ = run_subprocess(
        [
            "git",
            "-C",
            str(repo),
            "fetch",
            "--shallow-since",
            value,
            "origin",
            ref,
        ]
    )
    if returncode != 0:
        returncode, _, _ = run_subprocess(
            ["git", "-C", str(repo), "fetch", "--depth", "1", "origin", ref]
        )
        if returncode != 0:
            raise click.ClickException(
                f"Could not fetch '{branch}' history since {value} in {repo}."
            )
    sha = _rev_list_before(repo, value)
    if not sha:
        _deepen_until(repo, ref, lambda: _rev_list_before(repo, value))
        sha = _rev_list_before(repo, value)
    if not sha:
        raise click.ClickException(
            f"No commit on '{branch}' at or before '{value}' found in {repo}."
        )
    if track_ref:
        run_subprocess(["git", "-C", str(repo), "update-ref", ref, "FETCH_HEAD"])
    return sha, "detach", None


def _rev_list_before(repo, date):
    """Return the newest FETCH_HEAD-side commit at or before *date*."""
    _, out, _ = run_subprocess(
        ["git", "-C", str(repo), "rev-list", "-1", f"--before={date}", "FETCH_HEAD"]
    )
    return out.strip()


def _deepen_until(repo, ref, found, *, rounds=10, depth=500):
    """Deepen the shallow history of *ref* until *found()* is true.

    Each round doubles the requested depth so far-away targets resolve
    without unshallowing everything. A failing deepen means the history
    is complete — *found()* then settles the answer.
    """
    for _ in range(rounds):
        if found():
            return True
        returncode, _, _ = run_subprocess(
            ["git", "-C", str(repo), "fetch", "--deepen", str(depth), "origin", ref]
        )
        if returncode != 0:
            break
        depth *= 4
    return bool(found())


def _resolve_rev(repo, value, *, track_ref):
    """Resolve a branch-or-tag name in *repo* to ``(sha, mode, name)``."""
    for ref, mode in (
        (f"refs/heads/{value}", "branch"),
        (f"refs/tags/{value}", "detach"),
    ):
        returncode, _, _ = run_subprocess(
            ["git", "-C", str(repo), "fetch", "--depth", "1", "origin", ref]
        )
        if returncode != 0:
            continue
        sha = _git_rev(repo, "FETCH_HEAD^{commit}")
        if track_ref and mode == "branch":
            run_subprocess(["git", "-C", str(repo), "update-ref", ref, "FETCH_HEAD"])
        return sha, mode, value if mode == "branch" else None
    raise click.ClickException(
        f"Could not resolve '{value}' as a branch or tag in {repo} "
        "(for a commit, pass its full 40-character sha)."
    )


def _checkout_target(resolved, sha, mode, branch_name, *, force=False):
    """Fetch *sha* into the clone and switch it there.

    Staying on the same branch is a plain ``reset --hard``; switching or
    detaching uses ``git switch``, with ``--discard-changes`` when
    *force* lets a dirty tree be overwritten.
    """
    _git_fetch(resolved, sha, depth=1)
    current = _source_branch(resolved)
    if mode == "branch" and branch_name == current:
        args = ["reset", "--hard", sha]
    else:
        args = (
            ["switch", "-C", branch_name]
            if mode == "branch"
            else ["switch", "--detach"]
        )
        if force:
            args.insert(1, "--discard-changes")
        args.append(sha)
    returncode, _, stderr = run_subprocess(["git", "-C", str(resolved), *args])
    if returncode != 0:
        raise click.ClickException(
            f"Could not switch {resolved} to {sha}: {stderr.strip()}"
        )


def _git_fetch(repo, ref, *, depth=None, dry_run=False):
    """``git fetch origin <ref>`` in *repo*, raising a clear error on failure."""
    cmd = ["git", "-C", str(repo), "fetch"]
    if depth:
        cmd += ["--depth", str(depth)]
    cmd += ["origin", ref]
    returncode, _, stderr = run_subprocess(cmd, dry_run=dry_run)
    if not dry_run and returncode != 0:
        detail = stderr.strip().splitlines()
        raise click.ClickException(
            f"Could not fetch '{ref}' in {repo}: "
            f"{detail[-1] if detail else 'fetch failed'}"
        )


def _git_rev(repo, rev):
    """Return the sha *rev* resolves to in *repo*, or None."""
    returncode, out, _ = run_subprocess(
        ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", rev]
    )
    return out.strip() if returncode == 0 else None


def _git_dirty(repo):
    """Return True when *repo*'s working tree has uncommitted changes."""
    returncode, out, _ = run_subprocess(
        ["git", "-C", str(repo), "status", "--porcelain"]
    )
    return bool(out.strip()) if returncode == 0 else True


def _short(sha):
    """Abbreviate *sha* for reporting; tolerates None."""
    return (sha or "?")[:10]
