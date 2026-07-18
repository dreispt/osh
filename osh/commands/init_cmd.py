"""`osh init` command implementation.

Initialises a project directory for Osh by:
1. Ensuring the target directory exists.
2. Creating a `.osh/` sub-directory for configuration and links.
3. Creating a `.osh/config` file for branch-to-database mappings.
4. Detecting existing Odoo/Enterprise/design-themes source trees inside *target*;
   if found (and the selected edition allows it), creates symlinks in `.osh/`
   pointing to them.
5. If no sources are found, the central cache is used. The cache is a git
   mirror of the upstream repository, stored in `~/.cache/osh`. The requested
   version is fetched if missing, and then a shallow clone is made into the
   project under `.osh/odoo` and, depending on the edition, `.osh/enterprise`
   and `.osh/design-themes`.
6. Installing Odoo dependencies (and any project-level requirements.txt),
   installing the Odoo source in editable mode, and running a quick Odoo
   executable smoke test.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import click

from ..backends import InitBackend
from ..db import _record_run_target
from ..utils import (
    _git_shallow_clone,
    _is_git_url,
    _load_user_init_config,
    _save_user_init_setting,
)

DEFAULT_ODOO_URL = "https://github.com/odoo/odoo.git"
DEFAULT_ENTERPRISE_URL = "git@github.com:odoo/enterprise.git"
DEFAULT_THEMES_URL = "https://github.com/odoo/design-themes.git"
SOURCE_CACHE_DIR = Path.home() / ".cache" / "osh"


@click.command(name="init")
@click.argument("version", type=str)
@click.argument(
    "directory", required=False, type=click.Path(file_okay=False, path_type=Path)
)
@click.option(
    "--target",
    "backend_name",
    default="local",
    envvar="OSH_INIT_TARGET",
    help="Environment target to initialise: local virtualenv or a plugin backend.",
)
@click.option(
    "-c",
    "--odoo-source",
    help="Odoo source: an existing local directory or a git URL. "
    "Defaults to the central cache (populated from GitHub).",
)
@click.option(
    "-e",
    "--enterprise-source",
    help="Enterprise source: an existing local directory or a git URL. "
    "Defaults to the central cache (populated from GitHub).",
)
@click.option(
    "-t",
    "--themes-source",
    help="Design-themes source: an existing local directory or a git URL. "
    "Defaults to the central cache (populated from GitHub).",
)
@click.option(
    "--edition",
    type=click.Choice(["ce", "ee", "sh"], case_sensitive=False),
    default=None,
    envvar="OSH_INIT_EDITION",
    help="Edition to initialize: ce (Community), ee (Enterprise), "
    "sh (Odoo.sh with Enterprise + design-themes).",
)
@click.option(
    "--ce",
    "edition",
    flag_value="ce",
    help="Alias for --edition ce.",
)
@click.option(
    "--ee",
    "edition",
    flag_value="ee",
    help="Alias for --edition ee.",
)
@click.option(
    "--sh",
    "edition",
    flag_value="sh",
    help="Alias for --edition sh.",
)
@click.option(
    "--save",
    is_flag=True,
    help="Save the resolved edition to ~/.config/osh/config.toml as the default.",
)
@click.option(
    "--yes",
    "assume_yes",
    is_flag=True,
    help="Assume yes for interactive prompts; useful when a TTY is available but input is not desired.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show the planned actions without modifying anything.",
)
@click.option(
    "--service",
    help="Docker Compose service name (docker target only).",
)
@click.option(
    "--command",
    help="Command override for the docker target.",
)
@click.option(
    "--compose-file",
    help="Docker Compose file path (docker target only).",
)
@click.pass_context
def init(
    ctx,
    version,
    directory,
    backend_name,
    odoo_source,
    enterprise_source,
    themes_source,
    edition,
    save,
    assume_yes,
    dry_run,
    service,
    command,
    compose_file,
):
    """Initialise a project for the chosen target.

    VERSION: Odoo version to use (e.g., '19.0', 'saas-19.4', 'master')
    DIRECTORY: Project directory to initialise (defaults to current directory)
    """
    target = (directory or Path.cwd()).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)

    if ctx.get_parameter_source("edition") == click.core.ParameterSource.DEFAULT:
        user_cfg = _load_user_init_config()
        edition = user_cfg.get("edition") or edition or "ce"
    edition = (edition or "ce").lower()
    if save and edition:
        _save_user_init_setting("edition", edition)

    from ..plugin_loader import load_backends

    backends = load_backends()
    backend_cls = backends.get(backend_name)
    if backend_cls is None:
        raise click.ClickException(f"Unknown init target: {backend_name}")

    backend = backend_cls()
    result = backend.init(
        ctx,
        target,
        version=version,
        edition=edition,
        dry_run=dry_run,
        odoo_source=odoo_source,
        enterprise_source=enterprise_source,
        themes_source=themes_source,
        save=save,
        assume_yes=assume_yes,
        service=service,
        command=command,
        compose_file=compose_file,
    )

    if not dry_run:
        _record_run_target(target, backend_name)

    if result:
        click.echo(f"Initialised project directory at {target}")
    else:
        click.echo(
            f"Warning: project initialisation at {target} did not complete successfully.",
            err=True,
        )


@click.command(name="init-local")
@click.argument("version", type=str)
@click.argument(
    "directory", required=False, type=click.Path(file_okay=False, path_type=Path)
)
@click.option(
    "-c",
    "--odoo-source",
    help="Odoo source: an existing local directory or a git URL. "
    "Defaults to the central cache (populated from GitHub).",
)
@click.option(
    "-e",
    "--enterprise-source",
    help="Enterprise source: an existing local directory or a git URL. "
    "Defaults to the central cache (populated from GitHub).",
)
@click.option(
    "-t",
    "--themes-source",
    help="Design-themes source: an existing local directory or a git URL. "
    "Defaults to the central cache (populated from GitHub).",
)
@click.option(
    "--edition",
    type=click.Choice(["ce", "ee", "sh"], case_sensitive=False),
    default=None,
    envvar="OSH_INIT_EDITION",
    help="Edition to initialize: ce (Community), ee (Enterprise), "
    "sh (Odoo.sh with Enterprise + design-themes). "
    "May also be set in ~/.config/osh/config.toml ([init] edition = ...).",
)
@click.option(
    "--ce",
    "edition",
    flag_value="ce",
    help="Alias for --edition ce.",
)
@click.option(
    "--ee",
    "edition",
    flag_value="ee",
    help="Alias for --edition ee.",
)
@click.option(
    "--sh",
    "edition",
    flag_value="sh",
    help="Alias for --edition sh.",
)
@click.option(
    "--save",
    is_flag=True,
    help="Save the resolved edition to ~/.config/osh/config.toml as the default.",
)
@click.option(
    "--yes",
    "assume_yes",
    is_flag=True,
    help="Assume yes for interactive prompts; useful when a TTY is available but input is not desired.",
)
@click.pass_context
def init_local(
    ctx: click.Context,
    version: str,
    directory: Path | None,
    odoo_source: str | None,
    enterprise_source: str | None,
    themes_source: str | None,
    edition: str | None,
    save: bool,
    assume_yes: bool,
) -> None:  # noqa: D401
    """Initialise *directory* for an Odoo project.

    VERSION: Odoo version to use (e.g., '19.0', 'saas-19.4', 'master')
    DIRECTORY: Project directory to initialise (defaults to current directory)

    Init target (set with --target):

    \b
      local - Clone Odoo sources, create a Python virtualenv, install Odoo,
              and run an `odoo-bin --version` smoke test (default).
      docker - Do not clone or install. Only write ``.osh/docker.toml`` with
               the service, command and optional compose file for use with an
               existing Docker Compose stack.

    Edition mode (set with --edition, --ce, --ee, --sh, OSH_INIT_EDITION, or
    ~/.config/osh/config.toml). Only used by the local target:

    \b
      ce - Community only: no Enterprise or design-themes.
      ee - Include Enterprise sources.
      sh - Include Enterprise and design-themes sources.

    If no edition is supplied, it defaults to the value from
    ~/.config/osh/config.toml or OSH_INIT_EDITION, otherwise ce.

    Use --save to persist the resolved edition to ~/.config/osh/config.toml.

    Source resolution (applied separately for Odoo, Enterprise and design-themes,
    local target only):

    \b
      1. Explicit --odoo-source / --enterprise-source / --themes-source flag.
      2. Source tree already included in the project (when the edition allows it).
      3. Central cache under ~/.cache/osh (shallow clone from GitHub by default).

    A diagnostic summary is printed before any work is done; in interactive mode
    a single ``Proceed?`` prompt is shown. Sources are installed with enterprise
    first so SSH credential prompts happen early.

    The central cache is a shallow bare clone of the upstream repository. osh
    init fetches the requested version if it is missing from the cache, then
    makes a shallow clone into .osh/odoo and, depending on the edition, into
    .osh/enterprise and .osh/design-themes.

    If the final pip install step fails, the project directory, .osh
    configuration, source links, and virtualenv are still created so the
    environment remains usable and the install can be retried manually.

    After a successful install, a quick smoke test (`odoo-bin --version`) is
    run to confirm the Odoo executable launches. If the smoke test fails, the
    environment is still created but a warning is shown.

    Any `requirements.txt` at the project root is installed in addition to the
    Odoo source requirements.

    Examples:

    \b
      osh init 19.0
      osh init 19.0 --target docker --service odoo --compose-file devel.yaml
      osh init 19.0 --enterprise
      osh init 19.0 --sh
      osh init 19.0 --edition ee
      osh init 19.0 --sh --save
      osh init 19.0 ./another-project
      osh init 19.0 -c /path/to/odoo -e /path/to/enterprise
      osh init 19.0 -c /path/to/odoo -e /path/to/enterprise -t /path/to/design-themes
      osh init 19.0 -c git@github.com:myfork/odoo.git
    """
    target = (directory or Path.cwd()).expanduser().resolve()
    if not target.exists():
        click.echo(f"Creating directory {target}…", err=True)
        target.mkdir(parents=True, exist_ok=True)

    osh_dir = target / ".osh"
    osh_dir.mkdir(exist_ok=True)

    # Ensure osh config file exists
    config_path = osh_dir / "config"
    if not config_path.exists():
        config_path.touch()

    backend = LocalInitBackend()

    # ------------------------------------------------------------------
    # Resolve edition mode (only relevant for the local target)
    # ------------------------------------------------------------------
    if ctx.get_parameter_source("edition") == click.core.ParameterSource.DEFAULT:
        user_cfg = _load_user_init_config()
        edition = user_cfg.get("edition") or edition or "ce"
    edition = (edition or "ce").lower()
    if save and edition:
        _save_user_init_setting("edition", edition)

    backend.pre_init(
        ctx,
        target,
        version,
        edition=edition,
        save=save,
    )

    # ------------------------------------------------------------------
    # Build a source plan, show diagnostics, and confirm once
    # ------------------------------------------------------------------
    include_enterprise = edition in ("ee", "sh") or enterprise_source is not None
    include_themes = edition == "sh" or themes_source is not None

    source_defs = [
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

    source_plans = {
        name: _resolve_source(
            name, version, flag, _find_local_source(target, names, files), osh_dir, url
        )
        for name, flag, names, files, url in source_defs
    }

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

    if assume_yes:
        click.echo("Proceeding with --yes (skipping confirmation).", err=True)
    elif sys.stdin.isatty():
        if not click.confirm("Proceed?", default=True, abort=True):
            return
    else:
        click.echo("Proceeding in non-interactive mode.", err=True)

    # Install sources with enterprise first, so credential prompts happen early.
    sources: dict[str, Path | None] = {}
    for name in ("enterprise", "design-themes", "odoo"):
        if name not in source_plans:
            continue
        action, spec, _warning = source_plans[name]
        sources[name] = _install_source_plan(name, version, action, spec, osh_dir)

    if not sources.get("odoo"):
        raise click.ClickException("Odoo sources are required.")

    env_ready = backend.setup_environment(
        ctx,
        target,
        osh_dir,
        sources,
        version,
        edition=edition,
        save=save,
    )
    smoke_ok = True
    if env_ready:
        smoke_ok = backend.smoke_test(
            ctx,
            target,
            osh_dir,
            edition=edition,
            save=save,
        )
    backend.post_init(
        ctx,
        target,
        osh_dir,
        edition=edition,
        save=save,
    )

    _record_run_target(target, "local")

    if not env_ready or not smoke_ok:
        click.echo(
            f"Initialised project directory at {target} "
            "(Odoo setup incomplete; see warnings above).",
            err=True,
        )
    else:
        click.echo(f"Initialised project directory at {target}")


class LocalInitBackend(InitBackend):
    """Default ``osh init`` backend: create a Python virtualenv and install Odoo."""

    name = "local"
    label = "Local virtualenv"

    def pre_init(
        self, ctx: click.Context, target: Path, version: str, **options: Any
    ) -> None:
        """No-op for the local backend."""

    def setup_environment(
        self,
        ctx: click.Context,
        target: Path,
        osh_dir: Path,
        sources: dict[str, Path | None],
        version: str,
        **options: Any,
    ) -> bool:
        """Create a virtualenv and pip-install Odoo sources."""
        odoo_link = sources.get("odoo")
        venv_path = target / ".venv"
        if venv_path.exists():
            click.echo(f"Using existing virtual environment at {venv_path}", err=True)
        else:
            click.echo(f"Creating virtual environment at {venv_path}…", err=True)
            import venv

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

    def smoke_test(
        self, ctx: click.Context, target: Path, osh_dir: Path, **options: Any
    ) -> bool:
        """Run ``odoo --version`` from the virtualenv."""
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

    def post_init(
        self, ctx: click.Context, target: Path, osh_dir: Path, **options: Any
    ) -> None:
        """No-op for the local backend."""


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
    git_dir = resolved / ".git"
    if not git_dir.exists() and not (
        resolved.is_symlink() and (resolved.resolve() / ".git").exists()
    ):
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
        _git_shallow_clone(spec, version, link)
        return link

    if action == "cache":
        cache = _ensure_cache(name, version, str(spec))
        click.echo(f"Cloning {name} from cache into {link} (shallow)…", err=True)
        _git_shallow_clone(f"file://{cache}", version, link)
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
                break
            except subprocess.CalledProcessError as exc:
                if refspec == refspecs[-1]:
                    raise click.ClickException(
                        f"Could not fetch {name} {version} from {default_url}"
                    ) from exc
    return cache


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
            if not path.is_dir():
                continue
            if any(next(path.glob(pattern), None) is not None for pattern in files):
                return path.resolve()
    return None
