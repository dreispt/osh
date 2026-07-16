"""`osh test` command implementation.

Runs Odoo tests for project modules. If the test database does not exist it is
initialised with `-i`, then the tests are executed with `-u`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import click

from ...db import (
    _db_exists,
    _drop_db,
    _get_branch_db,
    _get_current_branch,
    _get_last_db,
    _sanitize_db_name,
)
from ...utils import (
    _find_odoo_executable,
    _find_project_root,
    _get_odoo_base_dir,
    _get_project_name,
    discover_addons_paths,
)


def _project_module_names(base: Path) -> list[str]:
    """Return module names found in the project addons paths, excluding Odoo core."""
    odoo_dir = _get_odoo_base_dir(base)
    module_paths = discover_addons_paths(base)
    names: list[str] = []
    for path in module_paths:
        if odoo_dir and (path == odoo_dir or odoo_dir in path.parents):
            continue
        if path.name.startswith(".") or path.name.startswith("__"):
            continue
        names.append(path.name)
    return sorted(set(names))


def _resolve_db(base: Path, current_db: bool, test_db_name: str | None) -> str:
    """Return the database name to use for testing."""
    if current_db:
        branch = _get_current_branch(base) or "default"
        db_name = _get_branch_db(base, branch) or _get_last_db(base)
        if not db_name:
            raise click.ClickException(
                "No database configured for this branch. Run 'osh run' or 'osh config db' first."
            )
        return db_name

    if test_db_name:
        return _sanitize_db_name(test_db_name)

    branch = _get_current_branch(base) or "default"
    if branch == "HEAD":
        # Detached HEAD; use a generic test suffix.
        branch = "commit"
    project_name = _sanitize_db_name(_get_project_name(base))
    return f"{project_name}-{_sanitize_db_name(branch)}-test"


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
    help="Drop the test database after the test run.",
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
    help="Print the commands that would be run without executing them.",
)
@click.pass_context
def test(
    ctx: click.Context,
    modules: tuple[str, ...],
    test_db: str | None,
    current_db: bool,
    test_all: bool,
    tags: str | None,
    dropdb: bool,
    http: bool,
    no_stop_after_init: bool,
    dry_run: bool,
) -> None:  # noqa: D401
    """Run Odoo tests for project modules.

    By default the test database is `<project>-<branch>-test`. If it does not
    exist, it is created with `-i` and then tests are run with `-u`.

    Examples:

    \b
      osh test
      osh test my_module
      osh test --all
      osh test --tags :TestClass.method
      osh test --current-db
      osh test --dropdb
      osh test --dry-run
    """

    base = _find_project_root()
    if base is None:
        raise click.ClickException(
            "Not inside an Osh project. Run 'osh init <version>' to create one."
        )

    exe = _find_odoo_executable(base)
    if not exe:
        raise click.ClickException(
            "Could not locate Odoo executable. Run 'osh init <version>' to set up the project."
        )

    if not modules and not test_all:
        modules = tuple(_project_module_names(base))
        if not modules:
            raise click.ClickException("No project modules found to test.")

    if not modules and test_all:
        modules = tuple(_project_module_names(base))
        if not modules:
            raise click.ClickException("No project modules found to test.")

    module_list = ",".join(modules)

    db_name = _resolve_db(base, current_db, test_db)

    need_install = not _db_exists(base, db_name)
    if need_install and current_db:
        raise click.ClickException(f"Current database '{db_name}' does not exist.")

    install_args: list[str] | None = None
    if need_install and not current_db:
        install_args = [exe, "-d", db_name, "-i", module_list]
        if not http:
            install_args.append("--no-http")
        if not no_stop_after_init:
            install_args.append("--stop-after-init")

    test_args = [exe, "-d", db_name, "-u", module_list, "--test-enable"]
    if not http:
        test_args.append("--no-http")
    if not no_stop_after_init:
        test_args.append("--stop-after-init")
    if tags:
        test_args.extend(["--test-tags", tags])

    if dry_run:
        if install_args:
            click.echo(f"Would run: {' '.join(install_args)}", err=True)
        click.echo(f"Would run: {' '.join(test_args)}", err=True)
        if dropdb and not current_db:
            click.echo(f"Would drop database: {db_name}", err=True)
        return

    if install_args:
        click.echo(f"Creating test database '{db_name}'...", err=True)
        try:
            subprocess.check_call(install_args)
        except subprocess.CalledProcessError as exc:
            raise click.ClickException(f"Failed to create test database: {exc}")

    click.echo(f"Running tests in '{db_name}' for modules: {module_list}", err=True)
    try:
        subprocess.check_call(test_args)
    except subprocess.CalledProcessError as exc:
        raise click.ClickException(f"Tests failed: {exc}")

    if dropdb and not current_db:
        click.echo(f"Dropping test database '{db_name}'...", err=True)
        _drop_db(base, db_name)
