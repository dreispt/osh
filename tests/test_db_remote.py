"""Tests for ``osh db remote`` and remote-name resolution in get/restore."""

import json
import os
from pathlib import Path

from click.testing import CliRunner

import osh.cli  # noqa: F401  (registers plugin group commands on `db`)
from osh.commands.db_cmd import db
from osh.plugins.osh_db_get.backup_cmd import get
from osh.plugins.osh_db_get.remotes import (
    get_remotes,
    newest_cache_for_remote,
    remote,
    resolve_remote,
)
from osh.plugins.osh_db_get.restore_cmd import restore


def _write_cached_backup(cache_dir, filename, source, data=b"x"):
    """Create a cached backup file plus its metadata sidecar."""
    path = cache_dir / filename
    path.write_bytes(data)
    meta = {"source": source, "format": "dump", "path": str(path)}
    Path(str(path) + ".meta.json").write_text(json.dumps(meta))
    return path


# remote add / list -------------------------------------------------------


def test_remote_add_and_list(in_project):
    runner = CliRunner()

    result = runner.invoke(remote, ["add", "prod", "db://proddb"])
    assert result.exit_code == 0, result.output

    result = runner.invoke(remote, ["list"])
    assert result.exit_code == 0, result.output
    assert "prod" in result.output
    assert "db://proddb" in result.output

    assert get_remotes(in_project) == {"prod": "db://proddb"}


def test_remote_add_rejects_bad_source(in_project):
    result = CliRunner().invoke(remote, ["add", "prod", "not-a-source"])
    assert result.exit_code != 0
    assert "Invalid backup source" in result.output


def test_remote_add_rejects_duplicate(in_project):
    runner = CliRunner()
    assert runner.invoke(remote, ["add", "prod", "db://a"]).exit_code == 0
    result = runner.invoke(remote, ["add", "prod", "db://b"])
    assert result.exit_code != 0
    assert "already exists" in result.output


def test_remote_list_empty(in_project):
    result = CliRunner().invoke(remote, ["list"])
    assert result.exit_code == 0, result.output
    assert "No remotes" in result.output


def test_remote_registered_under_db_group(in_project):
    """`osh db remote` is reachable through the db group."""
    result = CliRunner().invoke(db, ["remote", "add", "prod", "db://proddb"])
    assert result.exit_code == 0, result.output
    assert resolve_remote(in_project, "prod") == "db://proddb"


# get <remote> ------------------------------------------------------------


def test_get_resolves_remote_name(in_project, subprocess_run_capture):
    """`osh db get <remote>` fetches from the remote's stored source."""
    runner = CliRunner()
    assert runner.invoke(remote, ["add", "prod", "db://proddb"]).exit_code == 0

    subprocess_run_capture.stdout = b"PGDMP" + b"\x00" * 100
    result = runner.invoke(get, ["prod"])

    assert result.exit_code == 0, result.output
    cache_dir = in_project / ".osh" / "backups"
    meta_path = next(cache_dir.glob("*.meta.json"))
    meta = json.loads(meta_path.read_text())
    assert meta["source"] == "db://proddb"


def test_get_raw_source_still_works(in_project, subprocess_run_capture):
    """Unregistered scheme:// sources keep working alongside remotes."""
    subprocess_run_capture.stdout = b"PGDMP" + b"\x00" * 100
    result = CliRunner().invoke(get, ["db://otherdb"])
    assert result.exit_code == 0, result.output


# restore <remote> --------------------------------------------------------


def test_restore_remote_picks_newest_from_that_remote(in_project, patched_restore):
    """`osh db restore <remote>` restores that remote's newest cached backup."""
    runner = CliRunner()
    runner.invoke(remote, ["add", "prod", "db://proddb"])

    cache_dir = in_project / ".osh" / "backups"
    cache_dir.mkdir(parents=True)
    prod_old = _write_cached_backup(cache_dir, "prod_old.dump", "db://proddb")
    prod_new = _write_cached_backup(cache_dir, "prod_new.dump", "db://proddb")
    other = _write_cached_backup(cache_dir, "other.dump", "db://otherdb")
    # Make the other remote's backup the newest overall: remote restore must
    # still pick prod's newest, not the newest cache entry overall.
    now = 1_700_000_000
    os.utime(prod_old, (now - 200, now - 200))
    os.utime(prod_new, (now - 100, now - 100))
    os.utime(other, (now, now))

    result = runner.invoke(restore, ["prod"])

    assert result.exit_code == 0, result.output
    assert patched_restore["restore"] == [(prod_new, patched_restore["db_name"], False)]


def test_restore_remote_without_cache_errors(in_project, patched_restore):
    runner = CliRunner()
    runner.invoke(remote, ["add", "prod", "db://proddb"])

    result = runner.invoke(restore, ["prod"])

    assert result.exit_code != 0
    assert "osh db get prod" in result.output


def test_restore_unknown_name_falls_through(in_project, patched_restore):
    """A non-remote argument still resolves as a path/cache ref."""
    cache_dir = in_project / ".osh" / "backups"
    cache_dir.mkdir(parents=True)
    cached = _write_cached_backup(cache_dir, "some.dump", "db://x")

    result = CliRunner().invoke(restore, ["cache:1"])

    assert result.exit_code == 0, result.output
    assert patched_restore["restore"] == [(cached, patched_restore["db_name"], False)]


def test_restore_never_contacts_external_source(
    in_project, patched_restore, monkeypatch
):
    """`osh db restore` reads only the local cache — no fetching."""
    import urllib.request

    def _boom(*args, **kwargs):
        raise AssertionError("restore must not fetch external sources")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    monkeypatch.setattr("osh.plugins.osh_db_get.sources.https.urlopen", _boom)

    cache_dir = in_project / ".osh" / "backups"
    cache_dir.mkdir(parents=True)
    _write_cached_backup(cache_dir, "x.dump", "https://demo.odoo.com?db=prod")

    result = CliRunner().invoke(restore, [])

    assert result.exit_code == 0, result.output


def test_newest_cache_for_remote_not_a_remote(in_project):
    assert newest_cache_for_remote(in_project, "db://proddb") is None
