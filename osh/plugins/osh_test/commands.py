"""`osh test` command implementation.

Runs Odoo tests for project modules. It is a thin wrapper around `osh run`
that adds the test-specific arguments (`--test-enable`, `-u`/`-i`, etc.) and
lets `osh run` handle the target backend.
"""

import click

from ... import echo
from ...commands.run_cmd import run
from ...commons import discover_module_names, find_project_root
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
):  # noqa: D401
    """Run Odoo tests for project modules.

    The test database is `<project>-<branch>-test` by default. If it does not
    exist, modules are first installed with `-i` (without tests) and then the
    tests are run with `-u`. If ``--dropdb`` is given, the test database is
    dropped once before any install/update so the run always starts on a fresh
    database. If the database already exists and ``--dropdb`` is not given,
    modules are updated with `-u` and tested directly.

    Examples:

    \b
      osh test my_module
      osh test --all
      osh test --tags :TestClass.method
      osh test --current-db
      osh test --dropdb
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

    if current_db and not db_exists(base, db_name):
        raise click.ClickException(f"Current database '{db_name}' does not exist.")

    need_install = not current_db and (dropdb or not db_exists(base, db_name))

    if dropdb and not current_db and not dry_run:
        drop_db(base, db_name)

    if need_install:
        if dry_run and dropdb:
            echo.info("Would drop and recreate the test database first.", err=True)
        # Fresh database: first install modules without tests.
        _run_odoo(ctx, db_name, module_list, "-i", http, dry_run=dry_run)

    # Run tests by updating the modules.
    _run_odoo(
        ctx,
        db_name,
        module_list,
        "-u",
        http,
        test_enable=True,
        tags=tags,
        stop_after_init=not no_stop_after_init,
        dry_run=dry_run,
    )


def _run_odoo(
    ctx,
    db_name,
    module_list,
    mode,
    http,
    *,
    test_enable=False,
    tags=None,
    stop_after_init=True,
    dry_run=False,
):
    """Invoke `osh run` with the requested Odoo test/install arguments."""
    odoo_args = ["-d", db_name, mode, module_list]
    if test_enable:
        odoo_args.append("--test-enable")
    if not http:
        odoo_args.append("--no-http")
    if stop_after_init:
        odoo_args.append("--stop-after-init")
    if tags:
        odoo_args.extend(["--test-tags", tags])

    run_args = []
    if dry_run:
        run_args.append("--dry-run")
    run_args.extend(odoo_args)
    run_ctx = run.make_context(run.name, run_args, parent=ctx)
    run.invoke(run_ctx)
