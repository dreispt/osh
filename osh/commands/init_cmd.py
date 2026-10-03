"""`osh init` command implementation.

``osh init`` performs only the backend-independent project setup: ``.osh/``
and the project config, ``.odoorc`` migration, dev-friendly config and the
neutralize scripts. Backend setup is layered on top by ``osh <backend> init``
commands, which call :func:`base_init` and then :func:`run_backend_init`.
"""

from __future__ import annotations

import configparser
import contextlib
import os
import re
import shutil
import sys
from pathlib import Path

import click

from .. import echo
from ..backends import copy_odoo_rc_to_osh_conf
from ..cli_utils import handler_command
from ..common import (
    find_enclosing_project,
    find_nested_projects,
    setup_project_neutralize_scripts,
)
from ..config import get_init_parent, load_user_init_config, save_user_preference
from ..db import (
    get_active_backend_name,
    get_project_config,
    is_prod_project,
    set_active_backend_name,
    set_project_config,
    unset_project_config,
)
from ..handlers import CommandHandler
from ..utils.odoo_layout import find_odoo_executable
from ..utils.version import get_version_from_executable
from .helpers import Diagnostics


class Init(CommandHandler):
    """Initialise an Osh project directory (base setup only).

    VERSION: Odoo version to use (e.g., '19.0', 'saas-19.4', 'master').
    Optional — defaults to $OSH_INIT_VERSION, then the version recorded by a
    previous ``osh init`` in the project, then the saved configuration.
    DIRECTORY: Project directory to initialise (defaults to $OSH_PROJECT_DIR,
    then the current directory)

    Creates `.osh/` and the project configuration, migrates `.odoorc` and
    installs the neutralize scripts. Backend setup — virtualenv, Odoo
    sources, Docker stack — is done by the backend's own init command,
    e.g. `osh venv init` or `osh docker init`.

    Examples:

    \b
      osh init 19.0
      osh init 19.0 ./another-project
      osh init 19.0 --ee
      osh init 19.0 --dry-run
      osh init            # re-init using the recorded version
      osh init --prod     # server with Odoo already installed (host runtime)

    With VERSION omitted, DIRECTORY can only be given as a path containing
    a separator (e.g. './another-project') — a bare name is read as VERSION.

    `--prod` is for servers where the Odoo environment already exists and
    the host runtime is used: it implies `--no-dev` and `--yes`, detects
    VERSION from `odoo --version` when omitted, uses the config file
    `$ODOO_RC` points to (e.g. /etc/odoo.conf) in place, and records
    `init.prod = true` so destructive database commands require typing the
    database name to confirm. Once recorded, re-running `osh init` keeps the
    production settings.
    """

    # Command state is on ``self``: the parsed params (``version``,
    # ``directory``, ``edition``, ...) plus ``target`` as ``run()`` fills
    # it in.
    _cli_name = "init"

    version = None
    directory = None
    edition = None
    save = False
    assume_yes = False
    dry_run = False
    dev = True
    prod = False

    @click.argument("version", required=False, type=str)
    @click.argument(
        "directory",
        required=False,
        type=click.Path(file_okay=False, path_type=Path),
    )
    @click.option(
        "--edition",
        type=click.Choice(["ce", "ee", "sh"], case_sensitive=False),
        default=None,
        help="Edition to initialize: ce (Community), ee (Enterprise), "
        "sh (Odoo.sh with Enterprise + design-themes). "
        "Defaults to $OSH_INIT_EDITION, then the saved configuration.",
    )
    @click.option("--ce", "edition", flag_value="ce", help="Alias for --edition ce.")
    @click.option("--ee", "edition", flag_value="ee", help="Alias for --edition ee.")
    @click.option("--sh", "edition", flag_value="sh", help="Alias for --edition sh.")
    @click.option(
        "--dev/--no-dev",
        "dev",
        default=True,
        help="Add development-friendly Odoo config options "
        "(limit_time_cpu=0, limit_time_real=0) to .osh/odoo.conf. "
        "Use --no-dev to disable.",
    )
    @click.option(
        "--prod",
        is_flag=True,
        help="Production/sysadmin mode for an existing Odoo environment: "
        "implies --no-dev and --yes, detects the version from the installed "
        "Odoo and marks the project as production (init.prod).",
    )
    @click.option(
        "--save",
        is_flag=True,
        help="Save the resolved edition to "
        "~/.config/osh/config.toml as the default.",
    )
    @click.option(
        "--yes",
        "assume_yes",
        is_flag=True,
        help="Assume yes for interactive prompts; useful when a TTY "
        "is available but input is not desired.",
    )
    @click.option(
        "--dry-run",
        is_flag=True,
        help="Show the planned actions without modifying anything.",
    )
    def run(self):
        version, directory = _split_version_arg(self.version, self.directory)
        self.target = _init_target(directory)
        with _rollback_new_osh_dir(self.target):
            base_init(
                self.ctx,
                self.target,
                version=version,
                edition=self.edition,
                save=self.save,
                assume_yes=self.assume_yes,
                dry_run=self.dry_run,
                dev=self.dev,
                prod=self.prod,
            )
        if self.dry_run:
            echo.info(f"Dry run for project directory at {self.target}")
        elif self.prod:
            echo.info(f"Initialised production project directory at {self.target}")
            echo.friendly("Commands run on the host runtime ('osh runtime status').")
        else:
            echo.info(f"Initialised project directory at {self.target}")
            echo.friendly("Next steps:")
            echo.friendly(
                "  osh <runtime> init  # e.g. 'osh venv init' or 'osh docker init'"
            )


init = handler_command("init", Init)


def _init_target(directory):
    """Return the absolute project directory for ``osh init``.

    Defaults to ``$OSH_PROJECT_DIR``, then the current directory.
    """
    directory = directory or os.environ.get("OSH_PROJECT_DIR") or Path.cwd()
    return Path(directory).expanduser().resolve()


def _split_version_arg(version, directory):
    """Treat a lone positional holding a path separator as DIRECTORY.

    With VERSION optional, ``osh init ./another-project`` would otherwise
    fill VERSION and silently initialise the current directory. A version
    string never contains a path separator, so such a value is unambiguous;
    bare names without a separator stay versions.
    """
    if directory is None and version and any(sep in version for sep in "/\\"):
        return None, Path(version)
    return version, directory


@contextlib.contextmanager
def _rollback_new_osh_dir(target):
    """Remove ``target/.osh`` on failure when the wrapped init created it.

    A ``.osh`` dir left behind by an init that could not complete would
    still mark the directory as an Osh project. An existing ``.osh`` with
    content is a real environment and is never removed; an empty one holds
    no state and is treated as not pre-existing.
    """
    osh_dir = Path(target) / ".osh"
    existed = osh_dir.is_dir() and any(osh_dir.iterdir())
    try:
        yield
    except BaseException as exc:
        if not existed:
            if osh_dir.is_dir():
                shutil.rmtree(osh_dir, ignore_errors=True)
                if not osh_dir.exists():
                    echo.info("Removed incomplete '.osh' directory.", err=True)
        elif not _is_user_abort(exc):
            echo.info("Existing '.osh' directory was left untouched.", err=True)
        raise


def _is_user_abort(exc):
    """Return True when *exc* is a user-initiated abort needing no rollback note."""
    return isinstance(exc, click.Abort) or (
        isinstance(exc, click.ClickException) and exc.message == "Aborted."
    )


def base_init(
    ctx,
    target,
    *,
    version,
    edition,
    save,
    assume_yes,
    dry_run,
    dev,
    prod=False,
):
    """Common project setup shared by ``osh init`` and ``osh <runtime> init``.

    Creates the target directory and ``.osh/``, resolves the version and
    edition, migrates ``.odoorc``, applies dev-friendly config, records the
    ``[init]`` settings and installs the neutralize scripts. Returns the
    resolved ``(edition, version)`` pair for the runtime init to reuse.

    *prod* (also implied by a previously recorded ``init.prod``) disables
    the dev config and the confirmation prompts, removes zero time limits
    a previous dev init left in ``.osh/odoo.conf``, switches the project to
    the ``host`` runtime and detects the version from the installed Odoo
    when not given.
    """
    if not prod and is_prod_project(target):
        echo.info("Project is marked as production (init.prod); keeping that mode.")
        prod = True
    if prod:
        dev = False
        assume_yes = True
        if not version:
            version = _detect_installed_version(target)
    version = version or _resolve_version(
        target, assume_yes=assume_yes, dry_run=dry_run
    )
    echo.friendly(f"Welcome to Osh! Let's set up your Odoo {version} project.")

    enclosing = _check_nesting(target, assume_yes=assume_yes, dry_run=dry_run)

    if not (target / ".git").exists() and not dry_run:
        echo.warning(
            f"'{target}' is not a git repository. "
            "It is recommended to initialise Osh inside a git project."
        )
        if not assume_yes and not click.confirm("Continue anyway?", default=False):
            raise click.ClickException("Aborted.")

    if ctx.get_parameter_source("edition") == click.core.ParameterSource.DEFAULT:
        edition = _default_edition(target) or edition
        if not edition and not dry_run and not assume_yes and sys.stdin.isatty():
            edition = click.prompt(
                "Edition",
                type=click.Choice(["ce", "ee", "sh"], case_sensitive=False),
                default="ce",
            )
    edition = (edition or "ce").lower()
    if save and not dry_run:
        save_user_preference("edition", edition, section="init")

    echo.info(f"Using Odoo {version}")
    echo.info(f"Using {_EDITION_NAMES.get(edition, edition)} edition")

    if dry_run:
        echo.info(f"Would create .osh/ project configuration in {target}")
        return edition, version

    target.mkdir(parents=True, exist_ok=True)

    osh_dir = target / ".osh"
    osh_dir.mkdir(exist_ok=True)
    config_path = osh_dir / "config"
    if not config_path.exists():
        config_path.touch()

    osh_conf = copy_odoo_rc_to_osh_conf(target)
    if dev and osh_conf.parent != osh_dir:
        echo.info(
            f"Not adding development options to the external config {osh_conf}.",
            err=True,
        )
    elif dev:
        _write_dev_config(osh_conf)
    elif prod and osh_conf.parent == osh_dir:
        _remove_dev_limits(osh_conf)

    init_values = {"version": version, "edition": edition, "dev": dev}
    if prod:
        init_values["prod"] = True
    if enclosing is not None:
        # Acknowledge the nesting as intentional — diagnostics tools read
        # ``init.parent``. Stored relative so the config stays valid if the
        # checkout moves.
        init_values["parent"] = os.path.relpath(enclosing, target)
    set_project_config(target, "init", values=init_values)
    if prod:
        previous = get_active_backend_name(target, default=None)
        if previous and previous != "host":
            set_active_backend_name(target, "host")
            echo.info(f"Switched from the '{previous}' runtime to 'host'.")
    if enclosing is None and get_project_config(target, "init", "parent"):
        unset_project_config(target, "init", "parent")
    setup_project_neutralize_scripts(target, version)
    return edition, version


def run_backend_init(
    ctx,
    backend,
    target,
    *,
    version,
    edition,
    assume_yes,
    dry_run,
    **options,
):
    """Run the backend-specific part of ``osh <backend> init``.

    Diagnoses the target for *backend*, shows the planned actions, asks for
    confirmation and calls ``backend.init``. On success the backend is
    recorded as the project's active run backend.
    """
    diagnostics = backend.diagnose(
        target,
        ctx,
        phase="init",
        version=version,
        edition=edition,
        sections=backend.diagnose_sections_for_phase("init"),
        **options,
    )
    for warning_msg in diagnostics.warnings:
        echo.warning(warning_msg)
    for error_msg in diagnostics.errors:
        echo.error(error_msg)
    if diagnostics.errors and not dry_run:
        raise click.ClickException("\n".join(diagnostics.errors))

    todo = TodoPlan(diagnostics)
    todo.execute_plan(backend, backend.name)

    confirmed = False
    if not dry_run and not assume_yes and todo.plan and sys.stdin.isatty():
        if not click.confirm("Proceed with initialization?", default=True):
            raise click.ClickException("Aborted.")
        confirmed = True

    result = backend.init(
        target,
        version=version,
        edition=edition,
        dry_run=dry_run,
        assume_yes=assume_yes,
        confirmed=confirmed,
        todo=todo,
        **options,
    )

    if not dry_run:
        set_active_backend_name(target, backend.name)
        init_values = {"target": backend.name}
        init_values.update(
            {key: str(value) for key, value in options.items() if value is not None}
        )
        set_project_config(target, "init", values=init_values)
        echo.info(f"Runtime '{backend.name}' is ready.")

    return result


class TodoPlan:
    """Progress tracker for init steps.

    Attributes:
        diagnostics: Backend diagnostic results (warnings, errors, plan items)
        plan: List of planned action strings for progress display
        index: Current position in the plan (0-based, increments on each start())
    """

    def __init__(self, diagnostics: Diagnostics | None):
        self.diagnostics: Diagnostics | None = diagnostics
        self.plan: list[str] = list(diagnostics.plan) if diagnostics else []
        self.index: int = 0

    def add_plan(self, item: str) -> None:
        """Record a planned action for the init process."""
        self.plan.append(item)

    def execute_plan(self, backend, backend_name: str) -> None:
        """Add backend plans on top of the diagnostics plans and display them."""
        backend._add_init_plans(self)
        # Display planned actions
        if self.plan:
            echo.info(f"Planned actions for {backend_name}:")
            for item in self.plan:
                echo.info(f"  - {_bold_label(item)}")

    def start(self) -> None:
        """Print progress message and advance to next step.

        Only prints if there are remaining plan items to display.
        """
        if self.index < len(self.plan):
            self.index += 1
            echo.info(_bold_label(self.plan[self.index - 1]))


def _bold_label(item):
    """Return *item* with its ``Label:`` prefix bolded.

    ``\\x1b[22m`` turns bold off without resetting the colour
    ``echo.info`` wraps the line in; all codes are stripped off-TTY.
    """
    label, sep, rest = item.partition(": ")
    if not sep:
        return item
    return f"\x1b[1m{label}:\x1b[22m {rest}"


_EDITION_NAMES = {"ce": "Community", "ee": "Enterprise", "sh": "Odoo.sh"}


def _check_nesting(target, *, assume_yes, dry_run):
    """Guard against accidental nested or host-wide Osh projects.

    Returns the enclosing project path when *target* sits inside an
    existing Osh project, so the caller can record it as ``init.parent``.
    Aborts by default on unacknowledged nesting; a previously recorded
    ``init.parent`` matching the detected enclosing project means the
    nesting was intentional.
    """
    enclosing = find_enclosing_project(target)

    if enclosing is not None:
        env_dir = (enclosing / ".osh").resolve()
        try:
            target.resolve().relative_to(env_dir)
        except ValueError:
            pass
        else:
            raise click.ClickException(
                f"'{target}' is inside the Osh environment directory "
                f"'{env_dir}'. Initialise the project at the repository "
                "root instead."
            )
        if get_init_parent(target) != enclosing:
            echo.warning(
                f"'{target}' is inside the Osh project at '{enclosing}'. "
                "A nested project gets its own environment, and commands "
                "run inside it will no longer use the parent's."
            )
            if (
                not dry_run
                and not assume_yes
                and not click.confirm("Create a nested Osh project?", default=False)
            ):
                raise click.ClickException("Aborted.")

    if target == Path.home().resolve():
        echo.warning(
            f"'{target}' is your home directory. A '.osh' there would be "
            "picked up by every directory under it that has no project of "
            "its own."
        )
        if (
            not dry_run
            and not assume_yes
            and not click.confirm("Initialise an Osh project here?", default=False)
        ):
            raise click.ClickException("Aborted.")

    nested = find_nested_projects(target)
    if nested:
        echo.warning(
            "Existing Osh project(s) inside this directory: "
            + ", ".join(str(p) for p in nested)
            + ". Commands run inside them keep using their own environment."
        )

    return enclosing


def _write_dev_config(osh_conf):
    """Add development-friendly timeouts to the project's Odoo config."""
    odoo_cfg = configparser.ConfigParser()
    if osh_conf.exists():
        odoo_cfg.read(osh_conf, encoding="utf-8")
    if not odoo_cfg.has_section("options"):
        odoo_cfg.add_section("options")
    odoo_cfg.set("options", "limit_time_cpu", "0")
    odoo_cfg.set("options", "limit_time_real", "0")
    with osh_conf.open("w", encoding="utf-8") as f:
        odoo_cfg.write(f)


def _remove_dev_limits(osh_conf):
    """Remove the unlimited (``0``) timeouts ``_write_dev_config`` sets."""
    if not osh_conf.exists():
        return
    odoo_cfg = configparser.ConfigParser()
    odoo_cfg.read(osh_conf, encoding="utf-8")
    removed = [
        option
        for option in ("limit_time_cpu", "limit_time_real")
        if odoo_cfg.get("options", option, fallback=None) == "0"
    ]
    if not removed:
        return
    for option in removed:
        odoo_cfg.remove_option("options", option)
    with osh_conf.open("w", encoding="utf-8") as f:
        odoo_cfg.write(f)
    echo.info(f"Removed unlimited {', '.join(removed)} from {osh_conf}.", err=True)


def _detect_installed_version(target):
    """Return the Odoo version reported by the resolved executable, or None.

    Runs ``odoo --version`` on the executable ``find_odoo_executable``
    resolves (project venv, ``.osh/odoo`` sources, then ``PATH``) and maps
    its output (``Odoo Server 17.0``, ``Odoo Server saas~17.4``) to an Osh
    version string (``17.0``, ``saas-17.4``).
    """
    exe = find_odoo_executable(target)
    output = get_version_from_executable(exe) if exe else None
    version = _parse_odoo_version(output) if output else None
    if version:
        echo.info(f"Detected Odoo {version} from {exe}")
    return version


def _parse_odoo_version(text):
    """Return the Osh version string in ``odoo --version`` *text*, or None."""
    match = re.search(r"saas[~-](\d+\.\d+)", text)
    if match:
        return f"saas-{match.group(1)}"
    match = re.search(r"\b(\d+\.\d+)", text)
    return match.group(1) if match else None


def _resolve_version(target, *, assume_yes, dry_run):
    """Return the Odoo version to use when the VERSION argument is omitted.

    ``OSH_INIT_VERSION`` wins, then the version the project recorded on a
    previous ``osh init``, then the saved user default — the recorded project
    version takes precedence over the user default so a re-init never
    silently switches versions. When nothing resolves, prompt interactively;
    non-interactive runs fail since every later step depends on it.
    """
    version = (
        os.environ.get("OSH_INIT_VERSION")
        or get_project_config(target, "init", "version")
        or load_user_init_config().get("version")
    )
    # A TOML ``version = 19.0`` reads back as a float; normalise so the
    # recorded value and every consumer always see the same string.
    version = str(version).strip() if version is not None else ""
    if version:
        return version
    if not dry_run and not assume_yes and sys.stdin.isatty():
        return click.prompt("Odoo version")
    raise click.ClickException(
        "Missing VERSION. Pass the Odoo version (e.g. 'osh init 19.0'), or "
        "run inside a project that already records one."
    )


def _default_edition(target):
    """Return the default edition from the environment or saved configuration.

    ``OSH_INIT_EDITION`` is read explicitly rather than through Click's
    ``envvar``: the ``--ce``/``--ee``/``--sh`` aliases share the ``edition``
    parameter with ``--edition`` and are parsed last, so they would overwrite
    an environment-provided value with their own empty default.
    """
    return (
        os.environ.get("OSH_INIT_EDITION")
        or load_user_init_config().get("edition")
        or get_project_config(target, "init", "edition")
    )
