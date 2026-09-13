"""`osh shell` command implementation.

``osh shell`` enters the project's runtime environment (local virtualenv or
Docker container) with ``ODOO_RC`` and PostgreSQL connection variables already
set for the active branch/database. Other commands, such as ``osh odoo``, build
on top of it.
"""

import configparser

import click

from .. import db as db_module
from .. import echo
from ..backends import EnvSpec
from ..common import (
    find_project_root,
    get_odoo_config_path,
    get_osh_odoo_config_path,
    has_arg,
)
from .helpers import check_run_diagnostics


def build_dynamic_odoo_config(
    base, db_name, backend, *, conf_path=None, no_db_filter=False, extra_args=()
):
    """Build a branch/database-specific Odoo config file in ``.osh/cache/env``.

    The generated config starts from ``.osh/odoo.conf`` (or ``.odoorc``) and
    adds the discovered addons path, ``db_name`` and ``dbfilter`` so that
    ``odoo-bin`` and ``psql`` work inside the environment without extra flags.

    The *backend* parameter is used to build backend-specific addons paths
    (e.g., container paths for Docker backends).
    """
    if conf_path is None:
        cache_dir = base / ".osh" / "cache" / "env"
        cache_dir.mkdir(parents=True, exist_ok=True)
        branch = db_module.sanitize_db_name(db_module.resolve_branch(base, None))
        safe_db = db_module.sanitize_db_name(db_name) if db_name else "none"
        conf_path = cache_dir / f"{branch}-{safe_db}.conf"
    else:
        conf_path.parent.mkdir(parents=True, exist_ok=True)

    source = get_osh_odoo_config_path(base)
    if not source.exists():
        source = get_odoo_config_path(base)

    cfg = configparser.ConfigParser()
    if source.exists():
        cfg.read(source, encoding="utf-8")
    if not cfg.has_section("options"):
        cfg.add_section("options")

    if not has_arg(extra_args, "--addons-path"):
        addons_paths = backend.build_addons_paths(base, include_themes=True)
        if addons_paths:
            cfg.set("options", "addons_path", ",".join(str(p) for p in addons_paths))

    if db_name:
        cfg.set("options", "db_name", db_name)
        if not no_db_filter:
            cfg.set("options", "dbfilter", f"^{db_name}$")

    with conf_path.open("w", encoding="utf-8") as f:
        cfg.write(f)

    return conf_path


def prepare_env_context(
    base,
    backend,
    *,
    ctx=None,
    db_name=None,
    no_db_filter=False,
    extra_args=(),
    dry_run=False,
):
    """Build the dynamic Odoo config and environment variables for a backend.

    Returns ``(config_path, env_vars, db_name)``. ``config_path`` is ``None``
    when the user passed an explicit ``--config`` argument. ``env_vars``
    contains ``ODOO_RC`` and PostgreSQL connection variables when available.

    The database name is resolved without probing or prompting — commands
    that require an existing database (``osh odoo``) resolve it with
    ``resolve_db_name_for_run`` beforehand and pass it in.
    """
    explicit_config = has_arg(extra_args, "--config", short="-c")
    no_db_filter = no_db_filter or has_arg(extra_args, "--db-filter")
    if not db_name and not explicit_config:
        db_name = db_module.resolve_db_name(base)

    if db_name and not dry_run:
        db_module.set_last_db(base, db_name)

    conf_path = None
    if not explicit_config:
        conf_path = build_dynamic_odoo_config(
            base,
            db_name,
            backend,
            no_db_filter=no_db_filter,
            extra_args=extra_args,
        )

    env_vars = {}
    if conf_path:
        env_vars["ODOO_RC"] = str(conf_path)
    env_vars.update(db_module.get_pg_env(base))
    if db_name:
        env_vars["PGDATABASE"] = db_name

    return conf_path, env_vars, db_name


@click.command(
    name="shell",
    context_settings=dict(ignore_unknown_options=True),
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print the assembled command without executing it.",
)
@click.option(
    "--target",
    "backend_name",
    default="local",
    envvar="OSH_RUN_TARGET",
    help="Execution target: local host, managed venv, or a plugin backend.",
)
@click.option(
    "--compose-file",
    default=None,
    envvar="OSH_COMPOSE_FILE",
    help="Docker Compose file to use (e.g. devel.yaml for Doodba). "
    "Defaults to $OSH_COMPOSE_FILE.",
)
@click.argument("extra_args", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def shell(
    ctx,
    dry_run,
    backend_name,
    compose_file,
    extra_args,
):  # noqa: D401
    """Enter the project's runtime environment or run a command in it.

    Without arguments this opens an interactive shell in the active target
    (local host, virtualenv, or Docker container) with ``ODOO_RC`` and PostgreSQL
    connection variables (``PGHOST``, ``PGUSER``, ...) already configured for
    the current branch and database. Any arguments are passed through as a
    command to run inside the environment.

    Passing an explicit ``--config``/``-c`` or ``--db-filter`` argument
    suppresses the generated config and dbfilter, exactly as when calling
    ``odoo-bin`` directly.

    Examples:

    \b
      osh shell
      osh shell odoo --version
      osh shell psql
      osh shell --target docker odoo -i base
    """
    base = find_project_root(required=True)

    backend = db_module.resolve_backend(ctx, base, backend_name)
    db_module.set_project_config(base, "run", "target", backend.name)

    check_run_diagnostics(base, backend, ctx, compose_file=compose_file)

    args = list(extra_args)
    if args and args[0] == "--":
        args.pop(0)

    conf_path, env_vars, resolved_db = prepare_env_context(
        base,
        backend,
        ctx=ctx,
        db_name=parse_explicit_db(args),
        extra_args=args,
        dry_run=dry_run,
    )
    if conf_path:
        echo.info(f"Using config: {conf_path}")
    if resolved_db:
        echo.info(f"Using database: {resolved_db}")

    env_spec = EnvSpec(
        argv=args,
        env=env_vars,
        db_name=resolved_db,
        config_path=str(conf_path) if conf_path else None,
    )
    backend.env(ctx, base, env_spec, dry_run=dry_run)


def parse_explicit_db(extra_args):
    """Return the database name explicitly passed via -d/--database, if any."""
    for i, arg in enumerate(extra_args):
        if arg in ("-d", "--database"):
            value = extra_args[i + 1] if i + 1 < len(extra_args) else ""
            return value if value and not value.startswith("-") else None
        if arg.startswith("-d") and len(arg) > 2:
            return arg[2:]
        if arg.startswith("--database="):
            return arg.split("=", 1)[1]
    return None
