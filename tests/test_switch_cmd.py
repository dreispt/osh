"""Tests for the ``osh switch`` command."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from osh.commands.switch_cmd import switch
from osh.db import get_active_env, resolve_db_name

requires_git = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not available"
)


def _init_git(project):
    """Turn the tmp_project fixture into a real git repository."""
    for args in (
        ["init"],
        ["config", "user.email", "x@y"],
        ["config", "user.name", "x"],
    ):
        subprocess.run(["git", *args], cwd=project, check=True, capture_output=True)
    (project / "README").write_text("x")
    subprocess.run(["git", "add", "README"], cwd=project, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=project, check=True, capture_output=True
    )


def _git_current_branch(project):
    result = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _write_cached_backup(cache_dir, filename, source):
    path = cache_dir / filename
    path.write_bytes(b"x")
    meta = {"source": source, "format": "dump", "path": str(path)}
    Path(str(path) + ".meta.json").write_text(json.dumps(meta))
    return path


@pytest.fixture
def capture_get_restore(monkeypatch):
    """Capture ctx.invoke calls to the get/restore command functions."""
    calls = {"get": [], "restore": []}
    monkeypatch.setattr(
        "osh.plugins.osh_db_get.backup_cmd.get",
        lambda **kw: calls["get"].append(kw),
    )
    monkeypatch.setattr(
        "osh.plugins.osh_db_get.restore_cmd.restore",
        lambda **kw: calls["restore"].append(kw),
    )
    return calls


# No-argument status ------------------------------------------------------


def test_switch_no_args_shows_current(in_project):
    """`osh switch` with no NAME prints the same content as `osh db show`."""
    result = CliRunner().invoke(switch, [])

    assert result.exit_code == 0, result.output
    assert "Branch:" in result.output
    assert "Database:" in result.output
    assert "Exists:" in result.output


# Git projects ------------------------------------------------------------


@requires_git
def test_switch_git_existing_branch(in_project):
    """`osh switch <name>` runs git switch and reports the branch database."""
    _init_git(in_project)
    subprocess.run(
        ["git", "switch", "-c", "other"],
        cwd=in_project,
        check=True,
        capture_output=True,
    )

    result = CliRunner().invoke(switch, ["other"])

    assert result.exit_code == 0, result.output
    assert _git_current_branch(in_project) == "other"
    assert "Branch:   other" in result.output
    assert "Database: project-other" in result.output


@requires_git
def test_switch_git_create_branch(in_project):
    """`osh switch -c <name>` creates the branch with git switch -c."""
    _init_git(in_project)

    result = CliRunner().invoke(switch, ["feature-x", "--create"])

    assert result.exit_code == 0, result.output
    assert _git_current_branch(in_project) == "feature-x"
    assert "Database: project-feature-x" in result.output


@requires_git
def test_switch_git_unknown_branch_errors(in_project):
    _init_git(in_project)

    result = CliRunner().invoke(switch, ["no-such-branch"])

    assert result.exit_code != 0
    assert "no-such-branch" in result.output


# Git-less projects -------------------------------------------------------


def test_switch_gitless_sets_active_env(in_project):
    """Without git, `osh switch <name>` records the active environment."""
    result = CliRunner().invoke(switch, ["staging"])

    assert result.exit_code == 0, result.output
    assert get_active_env(in_project) == "staging"
    assert "Branch:   staging" in result.output
    assert "Database: project-staging" in result.output


def test_switch_gitless_create_accepted(in_project):
    """--create is accepted in git-less projects for interface symmetry."""
    result = CliRunner().invoke(switch, ["staging", "-c"])

    assert result.exit_code == 0, result.output
    assert get_active_env(in_project) == "staging"


def test_switch_gitless_affects_db_resolution(in_project):
    """After switching, `osh db` resolution follows the active environment."""
    CliRunner().invoke(switch, ["staging"])

    assert resolve_db_name(in_project) == "project-staging"


# --refresh ---------------------------------------------------------------


def test_switch_refresh_restores_newest_cache(in_project, capture_get_restore):
    """Bare --refresh restores the newest cached backup, without fetching."""
    result = CliRunner().invoke(switch, ["env2", "--refresh"])

    assert result.exit_code == 0, result.output
    assert capture_get_restore["get"] == []
    assert capture_get_restore["restore"] == [{"dump": None}]


def test_switch_refresh_remote(in_project, capture_get_restore):
    """--refresh=<remote> passes the remote name to restore (no fetch)."""
    result = CliRunner().invoke(switch, ["env2", "--refresh=prod"])

    assert result.exit_code == 0, result.output
    assert capture_get_restore["get"] == []
    assert capture_get_restore["restore"] == [{"dump": "prod"}]


def test_switch_refresh_source_fetches_first(in_project, capture_get_restore):
    """--refresh=<url> fetches the source before restoring."""
    result = CliRunner().invoke(switch, ["env2", "--refresh=db://srcdb"])

    assert result.exit_code == 0, result.output
    assert capture_get_restore["get"] == [{"source": "db://srcdb"}]
    assert capture_get_restore["restore"] == [{"dump": None}]


def test_switch_refresh_remote_end_to_end(in_project, patched_restore):
    """--refresh=<remote> restores that remote's newest cached backup."""
    from osh.plugins.osh_db_get.remotes import remote

    runner = CliRunner()
    runner.invoke(remote, ["add", "prod", "db://proddb"])

    cache_dir = in_project / ".osh" / "backups"
    cache_dir.mkdir(parents=True)
    prod = _write_cached_backup(cache_dir, "prod.dump", "db://proddb")

    result = runner.invoke(switch, ["env2", "--refresh=prod"])

    assert result.exit_code == 0, result.output
    assert patched_restore["restore"] == [(prod, patched_restore["db_name"], False)]
    assert patched_restore["created"] == [patched_restore["db_name"]]
