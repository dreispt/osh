"""`osh db sanitize` command provided by the osh_db_sanitize plugin."""

import click

from ... import echo
from ...common import find_project_root
from ...db import db_exists, psql_rows, resolve_db_name, sanitize_db_name
from ...handlers import CommandHandler
from ...sql_scripts import (
    echo_sql_scripts,
    filter_sql_scripts,
    resolve_sql_scripts,
    run_sql_scripts,
)
from . import sanitize_ops


class DbSanitize(CommandHandler):
    """Scrub PII and delete low-value rows from a database.

    Two steps run by default: **anonymize** rewrites personal data with
    deterministic placeholder values; **trim** deletes rows from big,
    low-value tables (logs, mail queues, old chatter). The database must
    exist and should be neutralized — sanitizing a non-sanitized dump is
    irreversible, so the command warns and asks for confirmation unless
    ``--force``.

    SQL scripts resolve at run time from layered sources — bundled
    defaults, ``~/.config/osh/<kind>/`` (central, all projects), then
    ``.osh/<kind>/`` (this project) — merged by filename, later layers
    overriding. ``--list`` shows the resolved set, ``--script``/``--skip``
    select from it, and ``[trim]`` entries in ``.osh/config.toml``
    override the trim watchlist.

    Examples:

    \b
      osh db sanitize
      osh db sanitize -d prod_copy
      osh db sanitize --list
      osh db sanitize --no-anonymize --keep-days 90 --vacuum
      osh db sanitize --table mail_message --keep-days 7
      osh db sanitize --script 000_default.sql --force
    """

    _cli_name = "db.sanitize"

    database = None
    no_anonymize = False
    no_trim = False
    keep_days = None
    tables = ()
    scripts = ()
    skips = ()
    list_only = False
    variables = ()
    vacuum = False
    dry_run = False
    force = False

    @click.option(
        "-d",
        "--database",
        help="Database to sanitize (defaults to the current branch's database).",
    )
    @click.option(
        "--no-anonymize",
        is_flag=True,
        help="Skip the PII anonymization step.",
    )
    @click.option(
        "--no-trim",
        is_flag=True,
        help="Skip the trim step.",
    )
    @click.option(
        "--keep-days",
        type=int,
        default=None,
        help="Days of rows to keep for 'age' trim policies (default: 30, "
        "or $OSH_KEEP_DAYS, or the [trim] keep_days project setting).",
    )
    @click.option(
        "--table",
        "tables",
        multiple=True,
        help="Restrict trimming to the named tables (repeatable).",
    )
    @click.option(
        "--script",
        "scripts",
        multiple=True,
        help="Run only the named resolved scripts (repeatable).",
    )
    @click.option(
        "--skip",
        "skips",
        multiple=True,
        help="Drop the named scripts from the resolved set (repeatable).",
    )
    @click.option(
        "--list",
        "list_only",
        is_flag=True,
        help="Print the resolved scripts with their source layer and exit.",
    )
    @click.option(
        "--var",
        "variables",
        multiple=True,
        metavar="NAME=VALUE",
        help="Inject a variable into the scripts as session setting "
        "osh.<name> (repeatable); scripts read it via "
        "current_setting('osh.<name>').",
    )
    @click.option(
        "--vacuum",
        is_flag=True,
        help="VACUUM ANALYZE the trimmed tables afterwards.",
    )
    @click.option(
        "--dry-run",
        is_flag=True,
        help="Preview the statements and sizes without writing anything.",
    )
    @click.option(
        "--force",
        is_flag=True,
        help="Sanitize without asking for confirmation.",
    )
    def run(self):
        self.vars = self.parse_variables()
        self.base = find_project_root(required=True)
        if self.list_only:
            self.list_scripts()
            return
        self.db_name = self.resolve_database()
        if not db_exists(self.base, self.db_name, ctx=self.ctx):
            raise click.ClickException(
                f"Database '{self.db_name}' does not exist — nothing to sanitize."
            )
        self.check_neutralized()
        if not self.no_anonymize:
            self.anonymize()
        if not self.no_trim:
            self.trim()

    def parse_variables(self):
        """Turn ``--var NAME=VALUE`` pairs into the injection dict."""
        variables = {}
        for item in self.variables:
            name, eq, value = item.partition("=")
            if not eq or not name:
                raise click.UsageError(f"--var expects NAME=VALUE, got '{item}'.")
            variables[name] = value
        return variables

    def list_scripts(self):
        """Print the resolved anonymize and trim scripts and exit."""
        for kind in ("anonymize", "trim"):
            scripts = resolve_sql_scripts(self.base, kind)
            if not scripts:
                continue
            echo.info(f"{kind}:", err=True)
            echo_sql_scripts(scripts, err=True)

    def resolve_database(self):
        """Return the database to sanitize (``-d`` or the branch default)."""
        db_name = (
            sanitize_db_name(self.database)
            if self.database
            else resolve_db_name(self.base, verbose=False)
        )
        if not db_name:
            raise click.ClickException("Could not resolve a target database name.")
        return db_name

    def check_neutralized(self):
        """Warn when the database does not look neutralized; confirm the run."""
        if not self.force and not self.dry_run:
            _, rows, _ = psql_rows(
                self.base,
                self.db_name,
                "SELECT value FROM ir_config_parameter "
                "WHERE key = 'database.is_neutralized'",
                ctx=self.ctx,
            )
            if not (rows and rows[0][0] == "true"):
                echo.warning(
                    f"Database '{self.db_name}' does not look neutralized — "
                    "anonymizing or trimming a production database is "
                    "irreversible."
                )
            click.confirm(
                f"Sanitize database '{self.db_name}'?", default=False, abort=True
            )

    def anonymize(self):
        """Run the resolved anonymize scripts against the target database."""
        scripts = filter_sql_scripts(
            resolve_sql_scripts(self.base, "anonymize"),
            only=self.scripts,
            skip=self.skips,
        )
        if not scripts:
            echo.info("No anonymize scripts to run.")
            return
        run_sql_scripts(
            self.base,
            "anonymize",
            self.db_name,
            scripts,
            variables=self.vars or None,
            dry_run=self.dry_run,
            ctx=self.ctx,
        )

    def trim(self):
        """Run the resolved trim scripts with the keep-days variable."""
        keep_days = sanitize_ops.resolve_keep_days(self.base, self.keep_days)
        scripts = sanitize_ops.filter_tables(
            filter_sql_scripts(
                resolve_sql_scripts(self.base, "trim"),
                only=self.scripts,
                skip=self.skips,
            ),
            self.tables,
        )
        if not scripts:
            echo.info("No trim scripts to run.")
            return
        before = (
            None
            if self.dry_run
            else sanitize_ops.database_size(self.base, self.db_name, ctx=self.ctx)
        )
        run_sql_scripts(
            self.base,
            "trim",
            self.db_name,
            scripts,
            variables={"keep_days": str(keep_days), **self.vars},
            dry_run=self.dry_run,
            ctx=self.ctx,
        )
        if self.vacuum:
            sanitize_ops.vacuum_tables(
                self.base,
                self.db_name,
                sanitize_ops.script_tables(scripts),
                ctx=self.ctx,
            )
        if not self.dry_run:
            after = sanitize_ops.database_size(self.base, self.db_name, ctx=self.ctx)
            echo.info(sanitize_ops.report_reclaimed(before, after), err=True)
