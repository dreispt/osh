"""`osh db` commands outside an Osh project.

A user in a plain directory — no ``.osh``, no project config, no managed
runtime — can still drive PostgreSQL through osh: ``db list`` shows every
database, ``db copy`` works with explicit names, ``db drop`` requires a
name, ``backup restore`` takes a plain dump file, and ``db exec`` runs on the
host with the ambient libpq environment (``PGHOST``/``PGUSER``/...).

Project-bound state degrades the Unix way: config reads return empty or
fallback values, config writes fail with a clear error, the runtime falls
back to ``host`` and there is no branch→database mapping, no backup
cache, no remotes and no project filestore.
"""

import json

import click
import pytest
from click.testing import CliRunner

from osh.cli_utils import handler_command, handler_group
from osh.commands.db_cmd import Db
from osh.common import env_root, find_project_repos, find_project_root
from osh.config import (
    get_project_config,
    load_project_config,
    read_project_config,
    save_project_config,
    set_project_config,
    unset_project_config,
)
from osh.db import (
    get_active_runtime_name,
    get_last_db,
    get_pg_env,
    resolve_branch,
    resolve_db_name,
    resolve_runtime,
    run_in_runtime,
    set_last_db,
)
from osh.plugins.osh_backup import restore_cmd as _restore_cmd
from osh.plugins.osh_backup.remotes import DbRemote
from osh.plugins.osh_backup.restore_cmd import DbRestore
from osh.plugins.osh_db_drop.drop_cmd import DbDrop
from osh.sql_scripts import resolve_sql_scripts
from tests.helpers import make_git_project

# ``backup list`` was renamed ``db backups`` by the osh 2.1 db-command
# rework; accept either class name so these tests survive the rename.
DbBackups = getattr(_restore_cmd, "DbBackups", _restore_cmd.BackupList)

db = handler_group("db", Db)
drop = handler_command("drop", DbDrop)
restore = handler_command("restore", DbRestore)
backups = handler_command("backups", DbBackups)
remote = handler_group("remote", DbRemote)


@pytest.fixture
def outside(tmp_path, monkeypatch):
    """chdir into a directory with no ``.osh`` and no project ancestor."""
    monkeypatch.chdir(tmp_path)
    assert find_project_root() is None
    return tmp_path


@pytest.fixture
def patched_restore_no_project(monkeypatch, pg_db):
    """``patched_restore`` for a directory outside any Osh project.

    Records the restore pipeline steps instead of running them; the target
    database name is never pre-mapped — ``backup restore`` must derive it.
    """
    from osh.commands.helpers import Diagnostics

    state = {
        "restore": [],
        "neutralize": [],
        "sql_neutralize": [],
        "neutralize_scripts": [],
        "dropped": [],
        "created": [],
        "db_name": None,
    }

    monkeypatch.setattr(
        "osh.plugins.osh_backup.restore_cmd.drop_db",
        lambda base, db, **kw: state["dropped"].append(db),
    )
    monkeypatch.setattr(
        "osh.plugins.osh_backup.restore_cmd.create_db",
        lambda base, db, **kw: state["created"].append(db),
    )
    monkeypatch.setattr(
        "osh.plugins.osh_backup.restore_ops.restore_dump",
        lambda base, dump_path, db_name, *, dry_run=False, **kw: state[
            "restore"
        ].append((dump_path, db_name, dry_run)),
    )
    monkeypatch.setattr(
        "osh.plugins.osh_backup.restore_cmd.get_database_version",
        lambda base, db, **kw: (19, 0),
    )
    monkeypatch.setattr(
        "osh.plugins.osh_backup.restore_cmd.find_odoo_executable",
        lambda base: None,
    )
    monkeypatch.setattr(
        "osh.plugins.osh_backup.restore_ops.neutralize_with_sql",
        lambda base, db, **kw: state["sql_neutralize"].append(db),
    )
    monkeypatch.setattr(
        "osh.plugins.osh_backup.restore_ops.run_project_neutralize_scripts",
        lambda base, db, scripts, **kw: state["neutralize_scripts"].append(scripts),
    )
    monkeypatch.setattr(
        "osh.plugins.osh_backup.restore_cmd.odoo",
        lambda **kwargs: state["neutralize"].append(kwargs),
    )
    monkeypatch.setattr(
        "osh.plugins.osh_backup.restore_cmd.check_run_diagnostics",
        lambda *args, **kwargs: Diagnostics(
            runtime="none", info={}, warnings=[], errors=[]
        ),
    )
    return state


# Shared helpers ------------------------------------------------------------


def test_project_config_reads_fall_back_without_a_project():
    """Config reads outside a project return empty stores and fallbacks."""
    cfg = load_project_config(None)
    assert cfg.has_section("db")
    assert cfg.items("db") == []
    assert get_project_config(None, "db", "last_db") is None
    assert get_project_config(None, "run", "runtime", fallback="host") == "host"
    assert read_project_config(None, "verbosity", fallback="friendly") == "friendly"


def test_project_config_writes_require_a_project():
    """Config writes outside a project fail, naming ``osh init``."""
    with pytest.raises(click.ClickException, match="osh init"):
        set_project_config(None, "db", "main", "mydb")
    with pytest.raises(click.ClickException, match="osh init"):
        unset_project_config(None, "db", "main")
    with pytest.raises(click.ClickException, match="osh init"):
        save_project_config(None, load_project_config(None))


def test_env_root_requires_a_project():
    """``env_root(None)`` is a hard error — ``.osh`` paths need a project."""
    with pytest.raises(click.ClickException, match="osh init"):
        env_root(None)


def test_database_name_helpers_degrade_without_a_project():
    """Name/runtime resolution outside a project yields ``None``/``host``."""
    assert resolve_db_name(None) is None
    assert resolve_branch(None, None) == "default"
    assert get_last_db(None) is None
    assert get_pg_env(None) == {}
    assert get_active_runtime_name(None) == "host"
    assert resolve_runtime(None).name == "host"
    assert find_project_repos(None) == []


def test_set_last_db_is_a_noop_without_a_project():
    """Recording the last database outside a project writes nothing."""
    set_last_db(None, "whatever")  # must not raise
    assert get_last_db(None) is None


def test_sql_scripts_skip_the_project_layer_without_a_project(tmp_path):
    """Neutralize scripts still resolve from bundled + user layers only."""
    scripts = resolve_sql_scripts(None, "neutralize")
    assert scripts
    assert {script.layer for script in scripts} <= {"bundled", "user"}
    # A project-layer script would have been picked up from .osh/.
    project_scripts = tmp_path / ".osh" / "neutralize"
    project_scripts.mkdir(parents=True)
    (project_scripts / "999_project_only.sql").write_text("-- x\n")
    names = {script.name for script in resolve_sql_scripts(None, "neutralize")}
    assert "999_project_only.sql" not in names


def test_run_in_runtime_passes_libpq_env_through(monkeypatch):
    """Ambient ``PG*`` variables reach commands run outside a project."""
    monkeypatch.setenv("PGHOST", "db.example.com")
    monkeypatch.setenv("PGUSER", "osh-test")
    returncode, stdout, _ = run_in_runtime(None, None, ["printenv", "PGHOST"])
    assert returncode == 0
    assert stdout.strip() == "db.example.com"
    returncode, stdout, _ = run_in_runtime(None, None, ["printenv", "PGUSER"])
    assert returncode == 0
    assert stdout.strip() == "osh-test"


# `osh db show` -------------------------------------------------------------


def test_db_show_outside_project_reports_no_mapping(outside):
    """`osh db show` outside a project explains there is no mapping."""
    result = CliRunner().invoke(db, ["show"])
    assert result.exit_code == 0, result.output
    assert "No Osh project found" in result.output
    assert "no project database mapping" in result.output


def test_db_show_outside_project_json(outside):
    """`osh db show --json` reports ``project: null`` outside a project."""
    result = CliRunner().invoke(db, ["show", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["project"] is None
    assert payload["database"] is None


def test_db_show_in_plain_git_repo_prints_branch(outside, tmp_path, monkeypatch):
    """`osh db show` inside a git repo without ``.osh`` still shows the branch."""
    repo = make_git_project(tmp_path / "repo")
    monkeypatch.chdir(repo)
    result = CliRunner().invoke(db, ["show"])
    assert result.exit_code == 0, result.output
    assert "Branch:" in result.output
    assert "master" in result.output or "main" in result.output


# `osh db list` ------------------------------------------------------------


def test_db_list_outside_project_lists_everything(outside, pg_db):
    """`osh db list` outside a project lists all databases, unfiltered."""
    mine = pg_db.create()
    other = pg_db.create()
    result = CliRunner().invoke(db, ["list"])
    assert result.exit_code == 0, result.output
    assert mine in result.output
    assert other in result.output


def test_db_list_outside_project_json(outside, pg_db):
    """`osh db list --json` reports ``project: null`` and all databases."""
    mine = pg_db.create()
    other = pg_db.create()
    result = CliRunner().invoke(db, ["list", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["project"] is None
    assert {mine, other} <= set(payload["databases"])


# `osh db copy` ------------------------------------------------------------


def test_db_copy_outside_project_with_explicit_names(outside, pg_db):
    """`osh db copy A B` works outside a project; no filestore is touched."""
    src = pg_db.create()
    dst = pg_db.name()
    pg_db.track(dst)
    result = CliRunner().invoke(db, ["copy", src, dst])
    assert result.exit_code == 0, result.output
    assert pg_db.exists(dst)
    assert "filestore" in result.output.lower()


def test_db_copy_current_flags_require_a_project(outside):
    """``--from-current``/``--to-current`` are usage errors without a project."""
    result = CliRunner().invoke(db, ["copy", "a", "b", "--from-current"])
    assert result.exit_code != 0
    assert "osh project" in result.output.lower()
    result = CliRunner().invoke(db, ["copy", "a", "b", "--to-current"])
    assert result.exit_code != 0
    assert "osh project" in result.output.lower()


# `osh db exec` ------------------------------------------------------------


def test_db_exec_outside_project_uses_host_and_libpq_env(outside, monkeypatch):
    """`osh db exec` runs on the host: no ODOO_RC, ambient PG* only."""
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.delenv("PGDATABASE", raising=False)
    calls = []
    monkeypatch.setattr(
        "osh.runtimes.os.execvpe",
        lambda exe, args, env: calls.append((exe, list(args), env)),
    )
    result = CliRunner().invoke(db, ["exec"])
    assert result.exit_code == 0, result.output
    exe, args, env = calls[0]
    assert (exe, args) == ("/bin/zsh", ["/bin/zsh"])
    assert "ODOO_RC" not in env
    assert "PGDATABASE" not in env


def test_db_exec_outside_project_explicit_database(outside, monkeypatch):
    """``osh db exec -d NAME`` exports ``PGDATABASE`` without a project."""
    monkeypatch.setenv("SHELL", "/bin/zsh")
    calls = []
    monkeypatch.setattr(
        "osh.runtimes.os.execvpe",
        lambda exe, args, env: calls.append((exe, list(args), env)),
    )
    result = CliRunner().invoke(db, ["exec", "-d", "mydb"])
    assert result.exit_code == 0, result.output
    assert calls[0][2]["PGDATABASE"] == "mydb"
    assert "ODOO_RC" not in calls[0][2]


def test_osh_exec_still_requires_a_project(outside):
    """Plain ``osh exec`` keeps requiring a project — only ``db exec`` frees it."""
    from osh.commands.shell_cmd import exec_cmd

    result = CliRunner().invoke(exec_cmd, [])
    assert result.exit_code == 0
    assert "Not inside an Osh project" in result.output


# `osh db drop` ------------------------------------------------------------


def test_db_drop_outside_project_requires_a_name(outside):
    """`osh db drop` without a name fails outside a project — no branch db."""
    result = CliRunner().invoke(drop, ["--force"])
    assert result.exit_code != 0
    assert "name" in result.output.lower()


def test_db_drop_outside_project_drops_named_db(outside, pg_db):
    """`osh db drop -d NAME --force` drops the database; no filestore/last_db."""
    name = pg_db.create()
    result = CliRunner().invoke(drop, ["-d", name, "--force"])
    assert result.exit_code == 0, result.output
    assert f"Dropped database '{name}'" in result.output
    assert not pg_db.exists(name)


# `osh backup restore` ---------------------------------------------------------


def test_db_restore_outside_project_derives_target_from_dump(
    outside, patched_restore_no_project, pg_db
):
    """`osh backup restore x.dump` outside a project restores into database ``x``."""
    dump = outside / "mybackup.dump"
    dump.write_bytes(b"PGDMP")
    result = CliRunner().invoke(restore, [str(dump)])
    assert result.exit_code == 0, result.output
    assert patched_restore_no_project["created"] == ["mybackup"]
    assert patched_restore_no_project["restore"] == [
        (dump.resolve(), "mybackup", False)
    ]
    # No local Odoo outside a project: the SQL neutralization path runs.
    assert patched_restore_no_project["sql_neutralize"] == ["mybackup"]
    assert patched_restore_no_project["neutralize"] == []


def test_db_restore_outside_project_target_db_wins(outside, patched_restore_no_project):
    """``--db NAME`` overrides the name derived from the dump file."""
    dump = outside / "whatever.dump"
    dump.write_bytes(b"PGDMP")
    result = CliRunner().invoke(restore, [str(dump), "--db", "explicit_target"])
    assert result.exit_code == 0, result.output
    assert patched_restore_no_project["created"] == ["explicit_target"]


def test_db_restore_outside_project_requires_a_dump_file(outside):
    """`osh backup restore` with no argument fails — there is no project cache."""
    result = CliRunner().invoke(restore, [])
    assert result.exit_code != 0
    assert "dump" in result.output.lower()


def test_db_restore_outside_project_rejects_cache_refs(outside):
    """``cache:<id>`` and remote names need a project — plain paths only."""
    result = CliRunner().invoke(restore, ["cache:1"])
    assert result.exit_code != 0
    assert "project" in result.output.lower()


def test_db_restore_outside_project_missing_file(outside):
    """A nonexistent dump path is a plain "file not found" error."""
    result = CliRunner().invoke(restore, ["/nonexistent/backup.dump"])
    assert result.exit_code != 0
    assert "not found" in result.output.lower()


def test_db_restore_outside_project_dry_run(outside, patched_restore_no_project):
    """`osh backup restore --dry-run` previews without touching the database."""
    dump = outside / "preview.dump"
    dump.write_bytes(b"PGDMP")
    result = CliRunner().invoke(restore, [str(dump), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert patched_restore_no_project["restore"] == [(dump.resolve(), "preview", True)]
    assert patched_restore_no_project["created"] == []


def test_db_restore_outside_project_force_gates_existing_target(
    outside, patched_restore_no_project, pg_db
):
    """An existing derived target still requires ``--force`` to be replaced."""
    name = pg_db.create()
    dump = outside / f"{name}.dump"
    dump.write_bytes(b"PGDMP")

    result = CliRunner().invoke(restore, [str(dump)])
    assert result.exit_code != 0
    assert "already exists" in result.output
    assert "--force" in result.output

    result = CliRunner().invoke(restore, [str(dump), "--force"])
    assert result.exit_code == 0, result.output
    assert patched_restore_no_project["dropped"] == [name]
    assert patched_restore_no_project["created"] == [name]


# Project-bound commands stay project-bound ---------------------------------


def test_db_set_still_requires_a_project(outside):
    """`osh db set` writes project config — still requires a project."""
    result = CliRunner().invoke(db, ["set", "mydb"])
    assert result.exit_code == 0
    assert "Not inside an Osh project" in result.output


def test_db_unset_still_requires_a_project(outside):
    """`osh db unset` writes project config — still requires a project."""
    result = CliRunner().invoke(db, ["unset"])
    assert result.exit_code == 0
    assert "Not inside an Osh project" in result.output


def test_db_backups_still_requires_a_project(outside):
    """`osh backup list` lists the project cache — still requires a project."""
    result = CliRunner().invoke(backups, [])
    assert result.exit_code == 0
    assert "Not inside an Osh project" in result.output


def test_db_remote_still_requires_a_project(outside):
    """`osh backup remote` manages project config — still requires a project."""
    result = CliRunner().invoke(remote, ["list"])
    assert result.exit_code == 0
    assert "Not inside an Osh project" in result.output
