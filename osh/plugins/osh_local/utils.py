"""Local ``osh init`` implementation helpers."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import venv
from pathlib import Path

import click

DEFAULT_ODOO_URL = "https://github.com/odoo/odoo.git"
DEFAULT_ENTERPRISE_URL = "git@github.com:odoo/enterprise.git"
DEFAULT_THEMES_URL = "https://github.com/odoo/design-themes.git"
SOURCE_CACHE_DIR = Path.home() / ".cache" / "osh"


def init_project(
    target: Path,
    version: str,
    edition: str,
    dry_run: bool,
    assume_yes: bool,
    odoo_source: str | None,
    enterprise_source: str | None,
    themes_source: str | None,
) -> bool | None:
    """Initialise *target* for an Odoo project using local sources."""
    _prepare_target_dir(target)
    osh_dir = target / ".osh"

    source_plans = _build_source_plans(
        target,
        osh_dir,
        version,
        edition,
        odoo_source,
        enterprise_source,
        themes_source,
    )
    _display_init_plan(target, edition, source_plans)

    if dry_run:
        click.echo("Dry run: no changes made.", err=True)
        return True
    if not _confirm_init(assume_yes):
        return None

    sources = _install_sources(source_plans, version, osh_dir)
    if not sources.get("odoo"):
        raise click.ClickException("Odoo sources are required.")

    env_ready = _setup_environment(target, sources)
    smoke_ok = _run_init_smoke_test(target, env_ready)

    if not env_ready or not smoke_ok:
        click.echo(
            f"Initialised project directory at {target} "
            "(Odoo setup incomplete; see warnings above).",
            err=True,
        )
    else:
        click.echo(f"Initialised project directory at {target}")
    return True


def _prepare_target_dir(target: Path) -> None:
    """Ensure *target* and its ``.osh`` subdirectory exist with a config file."""
    if not target.exists():
        click.echo(f"Creating directory {target}…", err=True)
        target.mkdir(parents=True, exist_ok=True)

    osh_dir = target / ".osh"
    osh_dir.mkdir(exist_ok=True)

    config_path = osh_dir / "config"
    if not config_path.exists():
        config_path.touch()


def _build_source_plans(
    target: Path,
    osh_dir: Path,
    version: str,
    edition: str,
    odoo_source: str | None,
    enterprise_source: str | None,
    themes_source: str | None,
) -> dict[str, tuple[str, str | Path, str | None]]:
    """Resolve the install plan for each Odoo source component."""
    include_enterprise = edition in ("ee", "sh") or enterprise_source is not None
    include_themes = edition == "sh" or themes_source is not None

    source_defs: list[tuple[str, str | None, tuple[str, ...], tuple[str, ...], str]] = [
        ("odoo", odoo_source, ("",), ("odoo-bin",), DEFAULT_ODOO_URL),
    ]
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

    return {
        name: _resolve_source(
            name, version, flag, _find_local_source(target, names, files), osh_dir, url
        )
        for name, flag, names, files, url in source_defs
    }


def _display_init_plan(
    target: Path,
    edition: str,
    source_plans: dict[str, tuple[str, str | Path, str | None]],
) -> None:
    """Print the planned init actions for review before execution."""
    click.echo(
        f"Will initialise project at {target} with edition '{edition}':", err=True
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

    venv_path = target / ".venv"
    click.echo(
        f"  virtualenv: {'use existing' if venv_path.exists() else 'create at'} {venv_path}",
        err=True,
    )


def _confirm_init(assume_yes: bool) -> bool:
    """Ask the user to confirm the init plan, or proceed automatically."""
    if assume_yes:
        click.echo("Proceeding with --yes (skipping confirmation).", err=True)
        return True
    if sys.stdin.isatty():
        return click.confirm("Proceed?", default=True, abort=True)
    click.echo("Proceeding in non-interactive mode.", err=True)
    return True


def _install_sources(
    source_plans: dict[str, tuple[str, str | Path, str | None]],
    version: str,
    osh_dir: Path,
) -> dict[str, Path | None]:
    """Execute the source plans and return the installed source links."""
    sources: dict[str, Path | None] = {}
    for name in ("enterprise", "design-themes", "odoo"):
        if name not in source_plans:
            continue
        action, spec, _warning = source_plans[name]
        sources[name] = _install_source_plan(name, version, action, spec, osh_dir)
    return sources


def _run_init_smoke_test(target: Path, env_ready: bool) -> bool:
    """Run the Odoo smoke test when the environment is ready."""
    if not env_ready:
        return True
    odoo_exe = _find_odoo_executable_in_venv(target / ".venv")
    if odoo_exe is None:
        click.echo(
            "Warning: Odoo executable not found in virtualenv. "
            "The environment is initialised but Odoo may not be usable.",
            err=True,
        )
        return False
    click.echo(f"Running quick Odoo smoke test ({odoo_exe})…", err=True)
    return _run_smoke_test(odoo_exe)


def _setup_environment(
    target: Path,
    sources: dict[str, Path | None],
) -> bool:
    """Create a virtualenv and pip-install Odoo sources."""
    odoo_link = sources.get("odoo")
    venv_path = target / ".venv"
    if venv_path.exists():
        click.echo(f"Using existing virtual environment at {venv_path}", err=True)
    else:
        click.echo(f"Creating virtual environment at {venv_path}…", err=True)
        try:
            venv.create(str(venv_path), with_pip=True)  # type: ignore[attr-defined]
        except AttributeError:  # pragma: no cover (py<3.9)
            builder = venv.EnvBuilder(with_pip=True)
            builder.create(str(venv_path))

    pip_exe = venv_path / ("Scripts" if os.name == "nt" else "bin") / "pip"

    requirements_file = odoo_link / "requirements.txt"
    if requirements_file.exists():
        click.echo(f"Installing requirements from {requirements_file}…", err=True)
        if not _pip_install(pip_exe, "install", "-r", str(requirements_file)):
            return False

    project_requirements = target / "requirements.txt"
    if project_requirements.exists():
        click.echo(
            f"Installing project requirements from {project_requirements}…",
            err=True,
        )
        if not _pip_install(pip_exe, "install", "-r", str(project_requirements)):
            return False

    click.echo(f"Installing Odoo from {odoo_link} into virtualenv…", err=True)
    return _pip_install(pip_exe, "install", "-e", str(odoo_link))


def _pip_install(pip_exe: Path, *args: str) -> bool:
    """Run pip with *args* and report failures; return True on success."""
    try:
        subprocess.check_call([str(pip_exe), *args])
        return True
    except subprocess.CalledProcessError as exc:
        if isinstance(exc.cmd, (list, tuple)):
            command = " ".join(shlex.quote(str(arg)) for arg in exc.cmd)
        else:
            command = str(exc.cmd)
        click.echo(
            f"Warning: pip install failed (exit status {exc.returncode}).\n\n"
            f"You can retry the command manually:\n\n  {command}\n",
            err=True,
        )
        return False


def _run_smoke_test(odoo_exe: Path) -> bool:
    """Run ``odoo --version`` and return True if it succeeds."""
    try:
        subprocess.run(
            [str(odoo_exe), "--version"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        return True
    except subprocess.CalledProcessError as exc:
        stdout = exc.stdout.decode("utf-8", errors="replace") if exc.stdout else ""
        click.echo(
            f"Warning: Odoo smoke test failed (exit status {exc.returncode}).\n"
            f"{stdout}\n"
            "The environment is initialised but Odoo may not be usable.",
            err=True,
        )
        return False
    except FileNotFoundError:
        click.echo(
            "Warning: Odoo executable could not be executed. "
            "The environment is initialised but Odoo may not be usable.",
            err=True,
        )
        return False


def _find_odoo_executable_in_venv(venv_path: Path) -> Path | None:
    """Return the Odoo executable inside *venv_path*, or None if not found."""
    bin_dir = venv_path / ("Scripts" if os.name == "nt" else "bin")
    for name in ("odoo", "odoo-bin"):
        exe = bin_dir / name
        if exe.is_file():
            return exe
    return None


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
        click.echo(f"Linking {name} → {spec}…", err=True)
        os.symlink(spec, link, target_is_directory=True)
        return link

    if action == "clone":
        click.echo(f"Cloning {name} from {spec} (shallow)…", err=True)
        _git_shallow_clone(spec, link, branch=version)
        return link

    if action == "cache":
        cache = _ensure_cache(name, version, str(spec))
        click.echo(f"Cloning {name} from cache into {link} (shallow)…", err=True)
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
        click.echo(f"Creating central {name} cache at {cache} (shallow)…", err=True)
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
        click.echo(f"Fetching {name} {version} into cache…", err=True)
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


def _get_venv_python(exe: str) -> Path | None:
    """Return the Python interpreter for the virtualenv containing *exe*.

    *exe* is expected to be an odoo or odoo-bin executable inside a
    ``<venv>/bin`` directory. Returns the matching ``python`` executable if it
    exists, otherwise None.
    """
    exe_path = Path(exe)
    python = exe_path.parent / "python"
    if python.is_file():
        return python
    python3 = exe_path.parent / "python3"
    return python3 if python3.is_file() else None
