"""Trim machinery for ``osh db sanitize``.

The watchlist maps table names to policies: ``truncate`` empties the
table, ``age:<column>`` deletes rows older than ``--keep-days``, and
``off`` (config only) removes a bundled entry. Truncates try
``TRUNCATE ONLY`` first and fall back to ``CASCADE`` when foreign keys
demand it — the cascade is what makes queue/log tables cleanable.
"""

import click

from ... import echo
from ...db import load_osh_config, psql, psql_rows
from ...termui import human_size

# Ordered: referencing tables come before the tables they point at.
DEFAULT_WATCHLIST = (
    ("ir_logging", "truncate"),
    ("sms_sms", "truncate"),
    ("auditlog_log_line", "truncate"),
    ("auditlog_log", "truncate"),
    ("queue_job", "truncate"),
    ("mail_mail", "truncate"),
    ("mail_notification", "age:create_date"),
    ("mail_tracking_value", "age:create_date"),
    ("mail_message", "age:create_date"),
)


def resolve_watchlist(base, keep_days, *, tables=()):
    """Return the effective ``[(table, sql)]`` plan for the trim step.

    Bundled defaults merge with ``[trim]`` overrides from
    ``.osh/config.toml``: a named table replaces the bundled policy,
    ``off`` removes it, new names append after the defaults. *tables*
    restricts the plan to the named tables.
    """
    policies = list(DEFAULT_WATCHLIST)
    configured_keep_days = None
    config = load_osh_config(base)
    if config.has_section("trim"):
        for name, value in config.items("trim"):
            if name == "keep_days":
                configured_keep_days = value
                continue
            if value == "off":
                policies = [(t, p) for t, p in policies if t != name]
            else:
                policy = _parse_policy(value)
                policies = [(t, p) for t, p in policies if t != name]
                policies.append((name, policy))
    days = keep_days if keep_days is not None else configured_keep_days or 30
    plan = [
        (table, _policy_sql(table, policy, int(days))) for table, policy in policies
    ]
    if tables:
        wanted = set(tables)
        unknown = wanted - {table for table, _ in plan}
        if unknown:
            raise click.UsageError(
                f"Table(s) not on the watchlist: {', '.join(sorted(unknown))}"
            )
        plan = [(t, s) for t, s in plan if t in wanted]
    return plan


def _parse_policy(value):
    """Validate a ``[trim]`` policy value."""
    if value == "truncate" or value.startswith("age:"):
        return value
    raise click.ClickException(
        f"Invalid trim policy '{value}' — expected 'truncate', "
        "'age:<date-column>' or 'off'."
    )


def _policy_sql(table, policy, keep_days):
    """Translate a policy into the SQL run for it."""
    if policy == "truncate":
        return "truncate"
    column = policy.split(":", 1)[1]
    return f"DELETE FROM {table} WHERE {column} < now() - interval '{keep_days} days'"


def table_exists(base, db_name, table, *, ctx=None):
    """Whether *table* exists in *db_name* (``to_regclass`` probe)."""
    _, rows, _ = psql_rows(
        base, db_name, f"SELECT to_regclass('{table}') IS NOT NULL", ctx=ctx
    )
    return rows and rows[0][0] == "t"


def table_size(base, db_name, table, *, ctx=None):
    """``pg_total_relation_size`` for *table* in bytes, or None."""
    _, rows, _ = psql_rows(
        base, db_name, f"SELECT pg_total_relation_size('{table}')", ctx=ctx
    )
    return int(rows[0][0]) if rows and rows[0][0] else None


def run_trim(base, db_name, plan, *, vacuum=False, dry_run=False, ctx=None):
    """Execute the trim *plan* and report sizes; returns reclaimed bytes.

    Absent tables are skipped. ``--dry-run`` prints each table present,
    its size and the exact SQL — no writes.
    """
    reclaimed = 0
    trimmed = []
    for table, sql in plan:
        if not table_exists(base, db_name, table, ctx=ctx):
            continue
        before = table_size(base, db_name, table, ctx=ctx)
        if dry_run:
            echo.info(f"Would trim {table} ({human_size(before)}): {sql}", err=True)
            continue
        _execute_policy(base, db_name, table, sql, ctx=ctx)
        after = table_size(base, db_name, table, ctx=ctx)
        delta = (before or 0) - (after or 0)
        reclaimed += max(delta, 0)
        trimmed.append(table)
        echo.info(
            f"Trimmed {table}: {human_size(before)} -> {human_size(after)}",
            err=True,
        )
    if vacuum and trimmed:
        _vacuum_tables(base, db_name, trimmed, ctx=ctx)
    elif vacuum and dry_run:
        echo.info("Would VACUUM ANALYZE trimmed tables.", err=True)
    return reclaimed


def _execute_policy(base, db_name, table, sql, *, ctx=None):
    """Run one policy; truncate falls back to CASCADE on FK errors."""
    if sql == "truncate":
        returncode, _, stderr = psql(base, db_name, f"TRUNCATE ONLY {table}", ctx=ctx)
        if returncode == 0:
            return
        if "foreign key" in stderr:
            echo.warning(
                f"{table} is referenced by foreign keys — " "truncating with CASCADE."
            )
            returncode, _, stderr = psql(
                base, db_name, f"TRUNCATE {table} CASCADE", ctx=ctx
            )
            if returncode == 0:
                return
        raise click.ClickException(f"Failed to truncate {table}: {stderr.strip()}")
    returncode, _, stderr = psql(base, db_name, sql, ctx=ctx)
    if returncode != 0:
        echo.warning(f"Could not trim {table}: {stderr.strip()}")


def _vacuum_tables(base, db_name, tables, *, ctx=None):
    """``VACUUM ANALYZE`` the tables that were trimmed."""
    echo.info(f"Vacuuming: {', '.join(tables)}", err=True)
    psql(
        base,
        db_name,
        "; ".join(f"VACUUM ANALYZE {table}" for table in tables),
        ctx=ctx,
    )
