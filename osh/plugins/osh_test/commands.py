"""`osh test` command implementation.

`osh test` is a thin wrapper around `osh odoo` that adds test-specific
options and generates the right `-i`/`-u`/`--test-enable` arguments. It runs
on the project's active backend (see ``osh <backend> activate``).
"""

import click

from ... import echo
from ...commands.odoo_cmd import OdooRun
from ...common import discover_module_names, find_project_root
from ...db import db_exists, drop_db, resolve_test_db_name
from ...handlers import CommandHandler


class TestRun(CommandHandler):
    """Run Odoo tests for project modules.

    This is a wrapper around `osh odoo` that chains two Odoo invocations:
    an install/update run without `--test-enable`, followed by an update run
    with `--test-enable` so tests execute on the already installed modules.
    It runs on the project's active backend; `--compose-file` is forwarded.

    The test database is `<project>-<branch>-test` by default. On a fresh
    database, modules are first installed with `-i <modules>` and then tested
    with `-u <modules> --test-enable`. On an existing database, modules are
    updated and then tested in the same way.

    Examples:

    \b
      osh test my_module
      osh test --all
      osh test --tags :TestClass.method
      osh test --current-db
      osh test --dropdb
      osh test --dry-run
    """

    _cli_name = "test"

    modules = ()
    test_db = None
    current_db = False
    test_all = False
    tags = None
    dropdb = False
    http = False
    no_stop_after_init = False
    dry_run = False
    compose_file = None

    @click.argument("modules", nargs=-1)
    @click.option(
        "--db",
        "test_db",
        help="Test database name (defaults to <project>-<branch>-test).",
    )
    @click.option(
        "--current-db",
        is_flag=True,
        help="Run tests on the current branch database instead of a " "test database.",
    )
    @click.option(
        "--all",
        "test_all",
        is_flag=True,
        help="Test all project modules.",
    )
    @click.option(
        "--tags",
        help="Test tags (e.g. /module:Class.method).",
    )
    @click.option(
        "--dropdb",
        is_flag=True,
        help="Drop the test database before running tests, then "
        "install modules on a fresh database.",
    )
    @click.option(
        "--http",
        is_flag=True,
        help="Run the HTTP server during tests.",
    )
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
        "--compose-file",
        default=None,
        envvar="OSH_COMPOSE_FILE",
        help="Docker Compose file to use (e.g. devel.yaml for Doodba). "
        "Defaults to $OSH_COMPOSE_FILE.",
    )
    def run(self):
        ctx = self.ctx
        modules = self.modules
        base = find_project_root(required=True)

        if not modules:
            if not self.test_all:
                raise click.ClickException(
                    "No modules specified. Pass module names or use --all."
                )
            modules = tuple(discover_module_names(base))
            if not modules:
                raise click.ClickException("No project modules found to test.")

        module_list = ",".join(modules)
        db_name = resolve_test_db_name(base, self.current_db, self.test_db)

        if (
            self.current_db
            and not self.dry_run
            and not db_exists(base, db_name, ctx=ctx)
        ):
            raise click.ClickException(f"Current database '{db_name}' does not exist.")

        # In dry-run mode we skip the live database check so tests don't prompt
        # for a PostgreSQL password when no credentials are configured. We
        # assume the install path so the plan is shown.
        if self.dry_run:
            need_install = not self.current_db
        else:
            need_install = not self.current_db and (
                self.dropdb or not db_exists(base, db_name, ctx=ctx)
            )

        if self.dropdb and not self.current_db:
            if self.dry_run:
                echo.info("Would drop and recreate the test database first.", err=True)
            else:
                drop_db(base, db_name, ctx=ctx)

        # Build base Odoo arguments shared between install and test invocations.
        base_odoo_args = ["-d", db_name]
        if not self.http:
            base_odoo_args.append("--no-http")

        test_odoo_args = []
        if self.tags:
            test_odoo_args.extend(["--test-tags", self.tags])

        odoo_kwargs = {
            "dry_run": self.dry_run,
            "compose_file": self.compose_file,
        }

        stop_arg = ["--stop-after-init"] if not self.no_stop_after_init else []

        # First invocation: install or update modules without running tests.
        # Run in wait mode so the backend returns control instead of
        # exec/replacing.
        install_mode = "-i" if need_install else "-u"
        install_args = base_odoo_args + [install_mode, module_list] + stop_arg
        OdooRun(
            self.ctx,
            extra_args=tuple(install_args),
            wait_for_exit=True,
            **odoo_kwargs,
        ).run()

        # Second invocation: update modules and run tests. This is the final
        # process, so the backend can exec/replace as usual.
        test_args = (
            base_odoo_args
            + ["-u", module_list, "--test-enable"]
            + stop_arg
            + test_odoo_args
        )
        OdooRun(
            self.ctx,
            extra_args=tuple(test_args),
            wait_for_exit=False,
            **odoo_kwargs,
        ).run()
