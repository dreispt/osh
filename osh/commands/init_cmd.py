"""`osh init` command implementation.

``osh init`` performs only the backend-independent project setup: ``.osh/``
and the project config, ``.odoorc`` migration, dev-friendly config and the
neutralize scripts. Backend setup is layered on top by ``osh <backend> init``
commands, which call :func:`base_init` and then :func:`run_backend_init`.
"""

from __future__ import annotations

import configparser
import os
import sys
from pathlib import Path

import click

from .. import echo
from ..backends import copy_odoo_rc_to_osh_conf
from ..common import setup_project_neutralize_scripts
from ..config import load_user_init_config, save_user_preference
from ..db import get_project_config, set_project_config
from .helpers import Diagnostics


@click.command(name="init")
@click.argument("version", type=str)
@click.argument(
    "directory", required=False, type=click.Path(file_okay=False, path_type=Path)
)
@click.option(
    "--edition",
    type=click.Choice(["ce", "ee", "sh"], case_sensitive=False),
    default=None,
    help="Edition to initialize: ce (Community), ee (Enterprise), "
    "sh (Odoo.sh with Enterprise + design-themes). "
    "Defaults to $OSH_INIT_EDITION, then the saved configuration.",
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
    "--dev/--no-dev",
    "dev",
    default=True,
    help="Add development-friendly Odoo config options (limit_time_cpu=0, "
    "limit_time_real=0) to .osh/odoo.conf. Use --no-dev to disable.",
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
@click.pass_context
def init(
    ctx,
    version,
    directory,
    edition,
    save,
    assume_yes,
    dry_run,
    dev=True,
):  # noqa: D401
    """Initialise an Osh project directory (base setup only).

    VERSION: Odoo version to use (e.g., '19.0', 'saas-19.4', 'master')
    DIRECTORY: Project directory to initialise (defaults to current directory)

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
    """
    target = (directory or Path.cwd()).expanduser().resolve()
    base_init(
        ctx,
        target,
        version=version,
        edition=edition,
        save=save,
        assume_yes=assume_yes,
        dry_run=dry_run,
        dev=dev,
    )
    if dry_run:
        echo.info(f"Dry run for project directory at {target}")
    else:
        echo.info(f"Initialised project directory at {target}")
        echo.friendly("Next steps:")
        echo.friendly(
            "  osh <backend> init  # e.g. 'osh venv init' or 'osh docker init'"
        )
        echo.friendly("  osh doctor          # Check your setup")


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
):
    """Common project setup shared by ``osh init`` and ``osh <backend> init``.

    Creates the target directory and ``.osh/``, resolves the edition,
    migrates ``.odoorc``, applies dev-friendly config, records the ``[init]``
    settings and installs the neutralize scripts. Returns the resolved
    edition name for the backend init to reuse.
    """
    target.mkdir(parents=True, exist_ok=True)

    echo.friendly(f"Welcome to Osh! Let's set up your Odoo {version} project.")

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

    echo.info(f"Using {_EDITION_NAMES.get(edition, edition)} edition")

    if dry_run:
        echo.info(f"Would create .osh/ project configuration in {target}")
        return edition

    osh_dir = target / ".osh"
    osh_dir.mkdir(exist_ok=True)
    config_path = osh_dir / "config"
    if not config_path.exists():
        config_path.touch()

    osh_conf = copy_odoo_rc_to_osh_conf(target)
    if dev:
        _write_dev_config(osh_conf)

    set_project_config(
        target,
        "init",
        values={"version": version, "edition": edition, "dev": dev},
    )
    setup_project_neutralize_scripts(target, version)
    return edition


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
        assume_yes=assume_yes or confirmed,
        todo=todo,
        **options,
    )

    if not dry_run:
        set_project_config(target, "run", "target", backend.name)
        init_values = {"target": backend.name}
        init_values.update(
            {key: str(value) for key, value in options.items() if value is not None}
        )
        set_project_config(target, "init", values=init_values)
        echo.info(f"Backend '{backend.name}' is ready.")

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
            total = len(self.plan)
            for i, item in enumerate(self.plan, 1):
                echo.info(f"  [{i}/{total}] {item}")

    def start(self) -> None:
        """Print progress message and advance to next step.

        Only prints if there are remaining plan items to display.
        """
        if self.index < len(self.plan):
            self.index += 1
            echo.info(f"[{self.index}/{len(self.plan)}] {self.plan[self.index - 1]}")


_EDITION_NAMES = {"ce": "Community", "ee": "Enterprise", "sh": "Odoo.sh"}


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
