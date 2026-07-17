"""`osh init` command implementation.

Initialises a project directory for Osh by:
1. Ensuring the target directory exists.
2. Creating a `.osh/` sub-directory for configuration and links.
3. Creating a `.osh/config` file for branch-to-database mappings.
4. Detecting existing Odoo/Enterprise/design-themes source trees inside *target*;
   if found (and the selected edition allows it), creates symlinks in `.osh/`
   pointing to them.
5. If no sources are found, asks to use a central cache. The cache is a git
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


def _find_odoo_executable_in_venv(venv_path: Path) -> Path | None:
    """Return the Odoo executable inside *venv_path*, or None if not found."""
    bin_dir = venv_path / ("Scripts" if os.name == "nt" else "bin")
    for name in ("odoo", "odoo-bin"):
        exe = bin_dir / name
        if exe.is_file():
            return exe
    return None


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


def _ensure_cache(name: str, version: str, default_url: str) -> Path:
    """Return the cached bare repo for *name*, creating or updating it as needed.

    The cache is populated with shallow fetches: only the requested version/branch
    is downloaded. Existing refs are never pruned.
    """
    cache = SOURCE_CACHE_DIR / f"{name}.git"
    refspec = f"refs/heads/{version}:refs/heads/{version}"
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
    return cache


def _resolve_source(
    name: str,
    source_flag: str | None,
    project_source: Path | None,
    osh_dir: Path,
    default_url: str,
) -> tuple[str, str | Path]:
    """Return the planned action and source spec for *name*.

    Actions are ``existing``, ``symlink``, ``clone`` or ``cache``.
    """
    link = osh_dir / name
    if link.exists() or link.is_symlink():
        return "existing", link

    if source_flag:
        local_path = Path(source_flag).expanduser().resolve()
        if not _is_git_url(source_flag) and local_path.is_dir():
            return "symlink", local_path
        return "clone", source_flag

    if project_source:
        return "symlink", project_source

    return "cache", default_url


def _describe_source_plan(name: str, action: str, spec: str | Path) -> str:
    """Return a human-readable description of a source plan entry."""
    if action == "existing":
        return f"  {name}: use existing {spec}"
    if action == "symlink":
        return f"  {name}: symlink from {spec}"
    if action == "clone":
        return f"  {name}: clone from {spec}"
    if action == "cache":
        return f"  {name}: clone from central cache ({spec})"
    return f"  {name}: (unknown action {action})"


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


def _ensure_source(
    name: str,
    version: str,
    source_flag: str | None,
    project_source: Path | None,
    osh_dir: Path,
    default_url: str,
) -> Path | None:
    """Return the path to a usable *name* source inside *osh_dir*.

    Resolution order:
    1. Explicit ``--odoo-source`` / ``--enterprise-source`` flag.
    2. Source tree already included in the project.
    3. Central cache (cloned from the cache mirror if missing).
    4. Interactive alternative (local path or git URL).
    """
    action, spec = _resolve_source(
        name, source_flag, project_source, osh_dir, default_url
    )

    if action == "cache" and sys.stdin.isatty():
        use_cache = click.confirm(
            f"{name.capitalize()} sources not found in project. "
            f"Use central cache (clone from {default_url} if missing)?",
            default=True,
            err=True,
        )
        if not use_cache:
            if not sys.stdin.isatty():
                return None
            spec = click.prompt(
                "Enter a local path or git URL for "
                f"{name} sources (leave empty to skip)",
                default="",
                show_default=False,
                err=True,
            ).strip()
            if not spec:
                click.echo(f"Skipping {name} sources.", err=True)
                return None
            local_path = Path(spec).expanduser().resolve()
            if not _is_git_url(spec) and local_path.is_dir():
                action, spec = "symlink", local_path
            else:
                action, spec = "clone", spec

    return _install_source_plan(name, version, action, spec, osh_dir)


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
        pip_failed = False
        try:
            requirements_file = odoo_link / "requirements.txt"
            if requirements_file.exists():
                click.echo(
                    f"Installing requirements from {requirements_file}…", err=True
                )
                subprocess.check_call(
                    [str(pip_exe), "install", "-r", str(requirements_file)]
                )

            project_requirements = target / "requirements.txt"
            if project_requirements.exists():
                click.echo(
                    f"Installing project requirements from {project_requirements}…",
                    err=True,
                )
                subprocess.check_call(
                    [str(pip_exe), "install", "-r", str(project_requirements)]
                )

            click.echo(f"Installing Odoo from {odoo_link} into virtualenv…", err=True)
            subprocess.check_call([str(pip_exe), "install", "-e", str(odoo_link)])

        except subprocess.CalledProcessError as exc:
            pip_failed = True
            if isinstance(exc.cmd, (list, tuple)):
                command = " ".join(shlex.quote(str(arg)) for arg in exc.cmd)
            else:
                command = str(exc.cmd)
            click.echo(
                f"Warning: pip install failed (exit status {exc.returncode}).\n\n"
                f"You can retry the command manually:\n\n  {command}\n",
                err=True,
            )

        return not pip_failed

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
        try:
            subprocess.run(
                [str(odoo_exe), "--version"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
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
        return True

    def post_init(
        self, ctx: click.Context, target: Path, osh_dir: Path, **options: Any
    ) -> None:
        """No-op for the local backend."""


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

    If no edition is supplied and stdin is a terminal, the user is prompted to
    pick one; otherwise it defaults to ce.

    Use --save to persist the resolved edition to ~/.config/osh/config.toml.

    Source resolution (applied separately for Odoo, Enterprise and design-themes,
    local target only):

    \b
      1. Explicit --odoo-source / --enterprise-source / --themes-source flag.
      2. Source tree already included in the project (when the edition allows it).
      3. Central cache under ~/.cache/osh (shallow clone from GitHub by default).
      4. Interactive prompt for a local path or git URL.

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

    source_plans: list[tuple[str, str, str | Path]] = []
    source_plans.append(
        (
            "odoo",
            *_resolve_source(
                "odoo",
                odoo_source,
                _find_local_source(target, ("",), ("odoo-bin",)),
                osh_dir,
                DEFAULT_ODOO_URL,
            ),
        )
    )
    if include_enterprise:
        source_plans.append(
            (
                "enterprise",
                *_resolve_source(
                    "enterprise",
                    enterprise_source,
                    _find_local_source(
                        target,
                        ("enterprise",),
                        ("*/__manifest__.py", "*/__openerp__.py"),
                    ),
                    osh_dir,
                    DEFAULT_ENTERPRISE_URL,
                ),
            )
        )
    if include_themes:
        source_plans.append(
            (
                "design-themes",
                *_resolve_source(
                    "design-themes",
                    themes_source,
                    _find_local_source(
                        target,
                        ("design-themes", "themes"),
                        ("*/__manifest__.py", "*/__openerp__.py"),
                    ),
                    osh_dir,
                    DEFAULT_THEMES_URL,
                ),
            )
        )

    click.echo(
        f"Will initialise project at {target} with edition '{edition}':", err=True
    )
    for name, action, spec in source_plans:
        click.echo(_describe_source_plan(name, action, spec), err=True)

    venv_path = target / ".venv"
    if venv_path.exists():
        click.echo(f"  virtualenv: use existing {venv_path}", err=True)
    else:
        click.echo(f"  virtualenv: create at {venv_path}", err=True)

    if sys.stdin.isatty():
        if not click.confirm("Proceed?", default=True, abort=True):
            return
    else:
        click.echo("Proceeding in non-interactive mode.", err=True)

    # Install sources with enterprise first, so credential prompts happen early.
    install_order = ["enterprise", "design-themes", "odoo"]
    plan_by_name = {name: (action, spec) for name, action, spec in source_plans}
    sources: dict[str, Path | None] = {}
    for name in install_order:
        if name not in plan_by_name:
            continue
        action, spec = plan_by_name[name]
        link = _install_source_plan(name, version, action, spec, osh_dir)
        sources[name] = link

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


@click.command(
    name="init",
    add_help_option=False,
    context_settings=dict(ignore_unknown_options=True),
)
@click.option(
    "--target",
    "backend_name",
    default="local",
    envvar="OSH_INIT_TARGET",
    help="Environment target to initialise: local virtualenv or a plugin backend.",
)
@click.argument("extra_args", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def init(ctx, backend_name, extra_args):
    """Initialise a project for the chosen target.

    ``osh init`` is a router that delegates to ``init-<target>`` commands
    (e.g. ``init-local``, ``init-docker``). All extra arguments are forwarded.

    Pass ``--help`` to see the options for the selected target, or run
    ``osh init-local --help`` / ``osh init-<target> --help`` directly.
    """
    if any(arg in ("-h", "--help") for arg in extra_args):
        target_cmd = ctx.parent.command.commands.get(f"init-{backend_name}")
        if target_cmd is None:
            click.echo(ctx.get_help(), err=False)
            return
        target_ctx = click.Context(target_cmd, info_name=target_cmd.name)
        click.echo(target_cmd.get_help(target_ctx))
        return

    target_cmd = ctx.parent.command.commands.get(f"init-{backend_name}")
    if target_cmd is None:
        raise click.ClickException(f"Unknown init target: {backend_name}")
    return target_cmd.main(list(extra_args), standalone_mode=False)
