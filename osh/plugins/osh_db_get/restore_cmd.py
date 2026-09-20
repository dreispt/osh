"""`osh db restore` command provided by the osh_db_get plugin."""

import traceback

import click

from ... import echo
from ...commands.helpers import check_run_diagnostics
from ...commands.odoo_cmd import odoo
from ...common import find_project_root
from ...db import (
    create_db,
    db_exists,
    drop_db,
    get_database_version,
    resolve_backend,
    resolve_db_name,
    sanitize_db_name,
    set_last_db,
)
from ...handlers import CommandHandler
from ...utils.odoo_layout import find_odoo_executable
from ...utils.version import get_version_tuple
from . import restore_ops
from .remotes import newest_cache_for_remote, newest_cache_for_source


# `osh db restore` handler — extensions subclass DbRestore and override
# step methods, calling super(). Command state is on ``self``: ``ctx``,
# the parsed params plus ``base``, ``backend``, ``dump_path`` and
# ``db_name`` as ``run()`` fills them in.
class DbRestore(CommandHandler):
    """Restore a backup into the current branch's database and neutralize it.

    With no DUMP argument, the newest backup from the project cache is used.
    Use `cache:<id>` to pick a specific entry shown by `osh db restore --list`.
    DUMP may also be a remote name (see `osh db remote`) or a backup source
    URL — the newest cached backup fetched from it is restored.

    PostgreSQL credentials are read from ``.osh/odoo.conf`` (or ``.odoorc``)
    for every spawned tool, so no process environment changes are needed.

    The restore tool is chosen based on the backup extension:

    \b
      .dump   -> pg_restore
      .sql    -> psql
      .sql.gz -> gunzip -c | psql
      .zip    -> unzip + psql + filestore copy

    Backup contents are streamed to the tool's standard input, so host file
    paths never reach the backend environment — this works the same on the
    host and inside a Docker container.

    For `.zip` backups, the filestore directory is copied into the configured
    Odoo `data_dir` under `filestore/<dbname>/`. If `data_dir` cannot be
    determined, `osh db restore` warns and continues without the filestore.

    After the dump is restored, the database is neutralized. Odoo 16.0+ uses
    `odoo-bin neutralize -d <db>`; older versions rely on `.osh/neutralize/`
    scripts.

    Once the restore (and neutralization) completes, plugins extending the
    ``db.restore`` handler through ``post_restore()`` run — e.g. to record
    module fingerprints in the restored database. Failures are reported as
    warnings; they cannot fail an already-completed restore.

    Neutralization hooks:

    Custom `.sql` scripts inside `.osh/neutralize/` run after the built-in
    neutralization step, in sorted order. Use numeric prefixes to control the
    order (e.g. `000_default.sql`, `100_anonymize_partners.sql`). Global
    defaults can be placed in `~/.config/osh/neutralize/` and are copied to
    `.osh/neutralize/` during `osh init`.

    Examples:

    \b
      osh db restore
      osh db restore cache:1
      osh db restore prod
      osh db restore https://my.odoo.com/web?db=prod
      osh db restore /path/to/backup.zip
      osh db restore /path/to/backup.zip --db prod_restore
      osh db restore /path/to/backup.sql.gz --force
      osh db restore /path/to/backup.sql.gz --db prod_restore --force
      osh db restore --list
    """

    _cli_name = "db.restore"

    dump = None
    list_backups = False
    limit = 20
    reverse = False
    force = False
    no_neutralize = False
    target_db = None
    dry_run = False

    @click.argument("dump", required=False)
    @click.option(
        "--list",
        "list_backups",
        is_flag=True,
        help="List cached backups instead of restoring.",
    )
    @click.option(
        "--limit",
        default=20,
        show_default=True,
        help="Maximum number of backups to show (with --list).",
    )
    @click.option(
        "--reverse",
        is_flag=True,
        help="List oldest backups first (with --list).",
    )
    @click.option(
        "--force",
        is_flag=True,
        help="Overwrite the target database if it already exists.",
    )
    @click.option(
        "--no-neutralize",
        is_flag=True,
        help="Skip neutralizing the database after restoring.",
    )
    @click.option(
        "-d",
        "--db",
        "target_db",
        default=None,
        help="Target database name to restore into (defaults to the "
        "branch database).",
    )
    @click.option(
        "--dry-run",
        is_flag=True,
        help="Print the steps that would be executed without running them.",
    )
    def run(self):
        self.base = find_project_root(required=True)
        if self.list_backups:
            restore_ops.list_cached_backups(
                self.base, limit=self.limit, reverse=self.reverse
            )
            return
        self.dump_path = self.resolve_dump_path()
        self.db_name = self.resolve_target_db()
        self.backend = resolve_backend(self.base)
        check_run_diagnostics(self.base, self.backend, self.ctx)
        self.prepare_target()
        self.restore_dump()
        if not self.no_neutralize:
            self.neutralize()
        self.run_post_restore()
        if not self.dry_run:
            set_last_db(self.base, self.db_name)
            self.report()

    def resolve_dump_path(self):
        """Resolve the DUMP argument to a local backup file path."""
        return (
            newest_cache_for_remote(self.base, self.dump)
            or newest_cache_for_source(self.base, self.dump)
            or restore_ops.resolve_backup_path(self.base, self.dump)
        )

    def resolve_target_db(self):
        """Return the database name to restore into."""
        db_name = (
            sanitize_db_name(self.target_db)
            if self.target_db
            else resolve_db_name(self.base, verbose=False)
        )
        if not db_name:
            raise click.ClickException("Could not resolve a target database name.")
        return db_name

    def prepare_target(self):
        """Drop an existing target database when ``--force`` allows it."""
        if not db_exists(self.base, self.db_name, ctx=self.ctx, dry_run=self.dry_run):
            return
        if not self.force:
            raise click.ClickException(
                f"Database '{self.db_name}' already exists. "
                "Use --force to overwrite."
            )
        if self.dry_run:
            echo.info(f"Would drop database '{self.db_name}'", err=True)
        else:
            drop_db(self.base, self.db_name, ctx=self.ctx)

    def restore_dump(self):
        """Create the target database and stream the dump into it."""
        if self.dry_run:
            echo.info(f"Would create database '{self.db_name}'", err=True)
            restore_ops.restore_dump(
                self.base, self.dump_path, self.db_name, dry_run=True
            )
        else:
            create_db(self.base, self.db_name, ctx=self.ctx)
            restore_ops.restore_dump(
                self.base, self.dump_path, self.db_name, dry_run=False, ctx=self.ctx
            )

    def neutralize(self):
        """Neutralize the restored database (Odoo command and/or SQL scripts).

        The neutralization method is chosen from the *database* version, not
        the *local* Odoo version. This lets a user restore an older dump
        (e.g. 14.0) into a newer project (e.g. 19.0) without the built-in
        ``odoo-bin neutralize`` failing on missing tables/columns.
        """
        if self.dry_run:
            # The database does not exist in dry-run mode, so just preview
            # the built-in neutralize command. The real method is decided
            # after restore.
            self.odoo_neutralize(dry_run=True)
            restore_ops.run_project_neutralize_scripts(
                self.base, self.db_name, dry_run=True, ctx=self.ctx
            )
            return

        db_version = get_database_version(self.base, self.db_name, ctx=self.ctx)
        exe = find_odoo_executable(self.base)
        local_version = get_version_tuple(exe) if exe else None

        use_odoo = (
            db_version is not None
            and db_version >= (16, 0)
            and local_version is not None
            and db_version == local_version
        )

        if use_odoo:
            self.odoo_neutralize(dry_run=False)
        else:
            if db_version is None:
                echo.warning(
                    f"Could not determine database version for '{self.db_name}'; "
                    "using SQL fallback neutralization."
                )
            elif local_version is not None and db_version != local_version:
                echo.warning(
                    f"Database is {db_version[0]}.{db_version[1]}, local Odoo is "
                    f"{local_version[0]}.{local_version[1]}; using SQL fallback "
                    "neutralization."
                )
            restore_ops.neutralize_with_sql(self.base, self.db_name, ctx=self.ctx)

        restore_ops.run_project_neutralize_scripts(
            self.base, self.db_name, dry_run=self.dry_run, ctx=self.ctx
        )

    def odoo_neutralize(self, *, dry_run):
        """Run ``odoo neutralize -d <db_name>`` through ``osh odoo``."""
        self.ctx.invoke(
            odoo,
            dry_run=dry_run,
            compose_file=None,
            no_db_filter=True,
            extra_args=("neutralize", "-d", self.db_name),
        )

    def run_post_restore(self):
        """Run ``post_restore`` extensions; failures warn, never fail.

        The database is fully restored at this point, so a failing
        extension is reported as a warning instead of failing the command.
        Under ``--dry-run`` no database exists — extensions are skipped.
        """
        if self.dry_run:
            return
        try:
            self.post_restore()
        except Exception as exc:
            echo.warning(f"post-restore step failed: {type(exc).__name__}: {exc}")
            echo.internal(
                f"Traceback for post-restore step:\n{traceback.format_exc()}",
                err=True,
            )

    def post_restore(self):
        """Extension point — runs after the restore and neutralization.

        Plugins subclass ``DbRestore``, override this and call
        ``super()``; ``self.db_name`` is the restored database. Failures
        are reported as warnings and cannot fail the completed restore.
        """

    def report(self):
        """Print the restore success message."""
        if self.no_neutralize:
            echo.info(
                f"Restored database '{self.db_name}' from {self.dump_path} "
                "(neutralization skipped)",
                err=True,
            )
        else:
            echo.info(
                f"Restored and neutralized database '{self.db_name}' "
                f"from {self.dump_path}",
                err=True,
            )
