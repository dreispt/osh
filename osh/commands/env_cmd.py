"""`osh env` command implementation.

``osh env`` enters the project's runtime environment (local virtualenv or
Docker container) with ``ODOO_RC`` and PostgreSQL connection variables already
set for the active branch/database. Other commands, such as ``osh odoo``, build
on top of it.
"""

import configparser
from pathlib import Path

import click

from .. import db as db_module
from .. import echo
from ..backends import EnvSpec
from ..common import (
    _has_arg,
    find_project_root,
    get_odoo_config_path,
    get_osh_odoo_config_path,
)
from ..utils.odoo_layout import build_addons_paths
from ..utils.plugin_loader import load_backends
from .helpers import collect_diagnostics


def _get_pg_env(base):
    """Return PostgreSQL connection variables from the Odoo config as a dict.

    Reads ``.osh/odoo.conf`` if it exists, otherwise falls back to ``.odoorc``.
    Maps ``db_host``, ``db_port``, ``db_user`` and ``db_password`` to the
    standard ``PGHOST``, ``PGPORT``, ``PGUSER`` and ``PGPASSWORD`` environment
    variables so tools like ``psql`` and ``pg_restore`` connect automatically.
    """
    odoo_rc = get_osh_odoo_config_path(base)
    if not odoo_rc.exists():
        odoo_rc = get_odoo_config_path(base)
    env = {}
    if not odoo_rc.exists():
        return env

    cfg = configparser.ConfigParser()
    cfg.read(odoo_rc, encoding="utf-8")
    if not cfg.has_section("options"):
        return env

    mapping = {
        "db_host": "PGHOST",
        "db_port": "PGPORT",
        "db_user": "PGUSER",
        "db_password": "PGPASSWORD",
    }
    options = cfg["options"]
    for key, var in mapping.items():
        value = options.get(key)
        if value:
            env[var] = value
    return env


def build_dynamic_odoo_config(
    base, db_name, backend_name, *, conf_path=None, no_db_filter=False, extra_args=()
):
    """Build a branch/database-specific Odoo config file in ``.osh/cache/env``.

    The generated config starts from ``.osh/odoo.conf`` (or ``.odoorc``) and
    adds the discovered addons path, ``db_name`` and ``dbfilter`` so that
    ``odoo-bin`` and ``psql`` work inside the environment without extra flags.

    For the Docker backend, addons paths are translated to their container
    mount point under ``/mnt/extra-addons``.
    """
    if conf_path is None:
        cache_dir = base / ".osh" / "cache" / "env"
        cache_dir.mkdir(parents=True, exist_ok=True)
        branch = db_module.get_current_branch(base) or "default"
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

    if not _has_arg(extra_args, "--addons-path"):
        addons_paths = build_addons_paths(base, include_themes=True)
        if addons_paths:
            if backend_name == "docker":
                container_paths = []
                for path in addons_paths:
                    try:
                        rel = path.relative_to(base)
                    except ValueError:
                        rel = Path(path.name)
                    container_paths.append(f"/mnt/extra-addons/{rel}")
                cfg.set("options", "addons_path", ",".join(container_paths))
            else:
                cfg.set(
                    "options", "addons_path", ",".join(str(p) for p in addons_paths)
                )

    if db_name:
        cfg.set("options", "db_name", db_name)
        if not no_db_filter:
            cfg.set("options", "dbfilter", f"^{db_name}$")

    with conf_path.open("w", encoding="utf-8") as f:
        cfg.write(f)

    return conf_path


def prepare_env_context(
    base,
    backend_name,
    *,
    db_name=None,
    no_db_filter=False,
    skip_config=False,
    extra_args=(),
):
    """Build the dynamic Odoo config and environment variables for a backend.

    Returns ``(config_path, env_vars, db_name)``. ``config_path`` is ``None``
    when ``skip_config`` is True or the user passed an explicit ``--config``
    argument. ``env_vars`` contains ``ODOO_RC`` and PostgreSQL connection
    variables when available.
    """
    explicit_config = _has_arg(extra_args, "--config", short="-c")
    no_db_filter = no_db_filter or _has_arg(extra_args, "--db-filter")
    if not db_name and not explicit_config and not skip_config:
        db_name = db_module.resolve_db_name(base, verbose=False)

    if explicit_config or skip_config:
        conf_path = None
    else:
        branch = db_module.sanitize_db_name(
            db_module.get_current_branch(base) or "default"
        )
        safe_db = db_module.sanitize_db_name(db_name) if db_name else "none"
        conf_path = base / ".osh" / "cache" / "env" / f"{branch}-{safe_db}.conf"
        conf_path = build_dynamic_odoo_config(
            base,
            db_name,
            backend_name,
            conf_path=conf_path,
            no_db_filter=no_db_filter,
            extra_args=extra_args,
        )

    env_vars = {}
    if conf_path:
        env_vars["ODOO_RC"] = str(conf_path)
    env_vars.update(_get_pg_env(base))
    if db_name:
        env_vars["PGDATABASE"] = db_name

    return conf_path, env_vars, db_name


@click.command(
    name="env",
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
    help="Do not set dbfilter in the generated config.",
)
@click.option(
    "--skip-config",
    is_flag=True,
    hidden=True,
    help="Skip generating the dynamic config file.",
)
@click.argument("extra_args", nargs=-1, type=click.UNPROCESSED)
@click.pass_context
def env(
    ctx,
    dry_run,
    backend_name,
    compose_file,
    no_db_filter,
    skip_config,
    extra_args,
):  # noqa: D401
    """Enter the project's runtime environment or run a command in it.

    Without arguments this opens an interactive shell in the active target
    (local virtualenv or Docker container) with ``ODOO_RC`` and PostgreSQL
    connection variables already configured for the current branch and
    database. Any arguments are passed through as a command to run inside the
    environment.

    Examples:

    \b
      osh env
      osh env odoo --version
      osh env psql
      osh env --target docker odoo -i base
    """
    base = find_project_root(required=True)

    backend_name = db_module.resolve_run_target(base, backend_name, ctx)
    db_module.set_project_config(base, "run", "target", backend_name)

    backends = load_backends()
    backend_cls = backends.get(backend_name)
    if backend_cls is None:
        raise click.ClickException(f"Unknown env target: {backend_name}")
    backend = backend_cls()

    diagnostics = collect_diagnostics(
        base,
        backend,
        ctx,
        target=backend_name,
        phase="run",
        compose_file=compose_file,
        sections=backend.diagnose_sections_for_phase("run"),
    )
    for warning_msg in diagnostics.warnings:
        echo.warning(warning_msg)
    if diagnostics.errors:
        raise click.ClickException("\n".join(diagnostics.errors))

    args = list(extra_args)
    if args and args[0] == "--":
        args.pop(0)

    explicit_db = _parse_explicit_db(args)
    db_name = explicit_db or diagnostics.info.get("Project", {}).get("dbname")
    if db_name and not dry_run:
        branch = diagnostics.info.get("Project", {}).get("git_branch", "default")
        db_module.set_project_config(
            base, "db", values={branch: db_name, "last": db_name}
        )

    conf_path, env_vars, resolved_db = prepare_env_context(
        base,
        backend_name,
        db_name=db_name,
        no_db_filter=no_db_filter,
        skip_config=skip_config,
        extra_args=args,
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


def _parse_explicit_db(extra_args):
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
