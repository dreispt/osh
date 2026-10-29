"""Trim helpers for ``osh db sanitize``.

Trimming is just resolved ``trim`` scripts — the bundled
``osh/data/trim/NN_<table>.sql`` set plus user/project layers. Script
names carry the table they clean, which is what ``--table`` and
``--vacuum`` select on. ``:keep_days`` inside scripts is filled from
``--keep-days`` or the ``[trim] keep_days`` project setting.
"""

import os

import click

from ... import echo
from ...db import load_osh_config, psql, psql_rows
from ...termui import human_size


def resolve_keep_days(base, keep_days):
    """Effective retention days: flag > ``$OSH_KEEP_DAYS`` > ``[trim] keep_days`` > 30."""
    if keep_days is not None:
        return keep_days
    env_value = os.environ.get("OSH_KEEP_DAYS")
    if env_value:
        return int(env_value)
    config = load_osh_config(base)
    if not config.has_section("trim"):
        return 30
    value = dict(config.items("trim")).get("keep_days")
    unknown = set(dict(config.items("trim"))) - {"keep_days"}
    if unknown:
        raise click.ClickException(
            f"Unknown [trim] option(s): {', '.join(sorted(unknown))} — "
            "only 'keep_days' is supported; per-table overrides are "
            "trim scripts now."
        )
    return int(value) if value else 30


def script_tables(scripts):
    """Table names carried by ``NN_<table>.sql`` script names."""
    tables = []
    for script in scripts:
        stem = (
            script.name[: -len(".sql")] if script.name.endswith(".sql") else script.name
        )
        _, sep, table = stem.partition("_")
        if sep:
            tables.append(table)
    return tables


def filter_tables(scripts, tables):
    """Keep only scripts named for the ``--table`` tables given."""
    if not tables:
        return scripts
    matched = [
        script for script in scripts if set(script_tables([script])) & set(tables)
    ]
    known = {t for script in scripts for t in script_tables([script])}
    unknown = set(tables) - known
    if unknown:
        raise click.UsageError(
            f"Table(s) not covered by any trim script: " f"{', '.join(sorted(unknown))}"
        )
    return matched


def table_exists(base, db_name, table, *, ctx=None):
    """Whether *table* exists in *db_name* (``to_regclass`` probe)."""
    _, rows, _ = psql_rows(
        base, db_name, f"SELECT to_regclass('{table}') IS NOT NULL", ctx=ctx
    )
    return rows and rows[0][0] == "t"


def database_size(base, db_name, *, ctx=None):
    """``pg_database_size`` for *db_name* in bytes, or None."""
    _, rows, _ = psql_rows(
        base, db_name, f"SELECT pg_database_size('{db_name}')", ctx=ctx
    )
    return int(rows[0][0]) if rows and rows[0][0] else None


def vacuum_tables(base, db_name, tables, *, ctx=None):
    """``VACUUM ANALYZE`` each existing table."""
    for table in tables:
        if table_exists(base, db_name, table, ctx=ctx):
            echo.info(f"Vacuuming {table}", err=True)
            psql(base, db_name, f"VACUUM ANALYZE {table}", ctx=ctx)


def report_reclaimed(before, after):
    """Human-readable reclaimed-size report line."""
    reclaimed = max((before or 0) - (after or 0), 0)
    return (
        f"Database size: {human_size(before)} -> {human_size(after)} "
        f"(reclaimed {human_size(reclaimed)})"
    )
