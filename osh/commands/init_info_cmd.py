"""`osh init info` command — resolved environment facts.

Read-only: reports the environment Osh has resolved for the current
project — roots, runtime, Odoo version/edition, branch → database
mapping, config file locations and interpreter details — so scripts and
AI agents get the answers in one call instead of assembling them from
``config show``, ``db show`` and friends. ``--json`` emits the same
facts as a stable machine-readable document.
"""

from pathlib import Path

import click

from .. import echo
from ..cli_utils import emit_json, handler_command
from ..common import (
    env_root,
    find_project_root,
    get_odoo_config_path,
    get_osh_odoo_config_path,
)
from ..config import get_docker_config_path, get_project_config_path
from ..db import (
    db_exists,
    get_active_runtime_name,
    get_last_db,
    get_project_config,
    resolve_branch,
    resolve_db_name,
    resolve_runtime,
)
from ..handlers import CommandHandler
from ..utils.version import detect_project_version


class InitInfo(CommandHandler):
    """Show the resolved Osh environment for this project.

    Prints the facts ``osh`` commands rely on — project and environment
    roots, active runtime, Odoo version and edition, the current
    branch's database, config file paths, and the venv interpreter
    when one exists — without changing anything. Use ``--json`` for a
    machine-readable report.

    Examples:

    \b
      osh init info          # human-readable summary
      osh init info --json   # same facts as JSON
    """

    _cli_name = "init.info"

    @click.option(
        "--json",
        "as_json",
        is_flag=True,
        help="Print the facts as JSON for machine consumption.",
    )
    def run(self):
        base = find_project_root(required=False) or Path.cwd()
        facts = _env_facts(self.ctx, base)
        if self.as_json:
            emit_json(facts)
            return
        for key, value in facts.items():
            if isinstance(value, dict):
                echo.info(f"{key}:")
                for sub_key, sub_value in value.items():
                    echo.info(f"  {sub_key}: {sub_value}")
            elif isinstance(value, list):
                echo.info(f"{key}: {', '.join(str(v) for v in value) or '-'}")
            else:
                echo.info(f"{key}: {value}")


def _env_facts(ctx, base):
    """Collect the resolved environment facts for project *base*."""
    env = env_root(base)
    runtime_name = get_active_runtime_name(base)
    runtime = resolve_runtime(base)
    db_name = resolve_db_name(base, verbose=False)
    facts = {
        "project": str(base),
        "env_root": str(env),
        "runtime": runtime_name,
        "runtime_class": type(runtime).__name__,
        "odoo_version": get_project_config(base, "init", "version")
        or _detected_version(base)
        or "unknown",
        "edition": get_project_config(base, "init", "edition") or "ce",
        "branch": resolve_branch(base, None),
        "database": db_name,
        "database_exists": db_exists(base, db_name, ctx=ctx),
        "last_database": get_last_db(base),
        "config_file": str(get_project_config_path(base)),
        "odoo_conf": str(get_osh_odoo_config_path(base)),
        "odoo_rc": str(get_odoo_config_path(base)),
    }
    docker_conf = get_docker_config_path(base)
    facts["docker_config"] = str(docker_conf) if docker_conf.exists() else None
    _add_existing_paths(facts, base, env)
    _add_venv_python(facts, env)
    _add_addons_path(facts, base, runtime)
    return facts


def _detected_version(base):
    """Return the heuristically detected Odoo version, or ``None``.

    Detection walks the project tree, which can hit unreadable
    directories outside a real project — treat that as "no hint".
    """
    try:
        return detect_project_version(base)[0]
    except OSError:
        return None


def _add_existing_paths(facts, base, env):
    """Mark which well-known paths exist on disk."""
    facts["initialized"] = (env / ".osh").is_dir()
    facts["odoo_conf_exists"] = get_osh_odoo_config_path(base).exists()


def _add_venv_python(facts, env):
    """Record the venv interpreter when ``<env>/.venv`` exists."""
    venv = env / ".venv"
    if not venv.is_dir():
        return
    facts["venv"] = str(venv)
    try:
        from ..plugins.osh_runtime_venv.utils import venv_python_version

        version, exe = venv_python_version(venv)
    except Exception:
        version, exe = None, None
    facts["python"] = {"version": version, "path": str(exe) if exe else None}


def _add_addons_path(facts, base, runtime):
    """Record the resolved addons path, empty when it cannot be built."""
    try:
        paths = runtime.build_addons_paths(base)
    except Exception:
        paths = []
    facts["addons_path"] = [str(p) for p in paths]


info = handler_command("info", InitInfo)
