"""`osh test` command implementation.

`osh test` is a thin wrapper around `osh odoo` that adds test-specific
options and generates the right `-i`/`-u`/`--test-enable` arguments. Standard
`osh odoo` options such as `--target` and `--compose-file` are accepted and
passed through.
"""

import click

from ... import echo
from ...commands.odoo_cmd import odoo
from ...common import discover_module_names, find_project_root
from ...db import db_exists, drop_db, resolve_test_db_name


@click.command(name="test")
@click.argument("modules", nargs=-1)
@click.option(
    "--db", "test_db", help="Test database name (defaults to <project>-<branch>-test)."
)
@click.option(
    "--current-db",
    is_flag=True,
    help="Run tests on the current branch database instead of a test database.",
)
@click.option("--all", "test_all", is_flag=True, help="Test all project modules.")
@click.option("--tags", help="Test tags (e.g. /module:Class.method).")
@click.option(
    "--dropdb",
    is_flag=True,
    help="Drop the test database before running tests, then install modules on a fresh database.",
)
@click.option("--http", is_flag=True, help="Run the HTTP server during tests.")
@click.option(
    "--no-stop-after-init",
    is_flag=True,
    help="Do not stop after init; keep the server running.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print the command that would be run without executing it.",
)
@click.option(
    "--target",
    "backend_name",
    default="local",
    envvar="OSH_RUN_TARGET",
    help="Execution target: local virtualenv or a plugin backend.",
)
@click.option(
    "--compose-file",
    default=None,
    help="Docker Compose file to use (e.g. devel.yaml for Doodba).",
)
@click.option(
    "--no-db-filter",
    is_flag=True,
    hidden=True,
    help="Do not inject --db-filter (for odoo command).",
)
@click.option(
    "--skip-config",
    is_flag=True,
    hidden=True,
    help="Skip config file (for odoo subcommands).",
)
@click.pass_context
def test(
    ctx,
    modules,
    test_db,
    current_db,
    test_all,
    tags,
    dropdb,
    http,
    no_stop_after_init,
    dry_run,
    backend_name,
    compose_file,
    no_db_filter,
    skip_config,
):  # noqa: D401
    """Run Odoo tests for project modules.

    This is a wrapper around `osh odoo` that assembles a single Odoo invocation
    with the right `-i`/`-u`, `--test-enable`, `--dropdb` and `--test-tags`
    flags. Standard `osh odoo` options such as `--target` and `--compose-file`
    are accepted and forwarded.

    The test database is `<project>-<branch>-test` by default. On a fresh
    database, modules are installed and tests run in one invocation with
    `-i <modules> --test-enable`. On an existing database, modules are updated
    with `-u <modules> --test-enable`.

    Examples:

    \b
      osh test my_module
      osh test --all
      osh test --tags :TestClass.method
      osh test --current-db
      osh test --dropdb
      osh test --target docker
      osh test --dry-run
    """

    base = find_project_root(required=True)

    if not modules:
        if not test_all:
            raise click.ClickException(
                "No modules specified. Pass module names or use --all."
            )
        modules = tuple(discover_module_names(base))
        if not modules:
            raise click.ClickException("No project modules found to test.")

    module_list = ",".join(modules)
    db_name = resolve_test_db_name(base, current_db, test_db)

    if current_db and not dry_run and not db_exists(base, db_name):
        raise click.ClickException(f"Current database '{db_name}' does not exist.")

    # In dry-run mode we skip the live database check so tests don't prompt for
    # a PostgreSQL password when no credentials are configured. We assume the
    # install path so the plan is shown.
    if dry_run:
        need_install = not current_db
    else:
        need_install = not current_db and (dropdb or not db_exists(base, db_name))

    if dropdb and not current_db:
        if dry_run:
            echo.info("Would drop and recreate the test database first.", err=True)
        else:
            drop_db(base, db_name)

    # Build base Odoo arguments shared between install and test invocations.
    base_odoo_args = ["-d", db_name]
    if not http:
        base_odoo_args.append("--no-http")

    test_odoo_args = []
    if tags:
        test_odoo_args.extend(["--test-tags", tags])

    odoo_kwargs = {
        "dry_run": dry_run,
        "backend_name": backend_name,
        "compose_file": compose_file,
        "no_db_filter": no_db_filter,
        "skip_config": skip_config,
    }

    # Install on a fresh database or update on an existing one, enabling
    # tests for the installed/updated modules. A single invocation lets the
    # backend exec/replace the process without losing the test run.
    mode = "-i" if need_install else "-u"
    odoo_args = (
        base_odoo_args
        + [mode, module_list, "--test-enable"]
        + (["--stop-after-init"] if not no_stop_after_init else [])
        + test_odoo_args
    )
    ctx.invoke(odoo, extra_args=odoo_args, **odoo_kwargs)
