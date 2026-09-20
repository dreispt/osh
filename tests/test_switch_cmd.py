"""Tests for the ``osh switch`` command."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from osh.commands.switch_cmd import switch
from osh.common import find_project_repos
from osh.db import get_active_env, get_current_branch, resolve_db_name

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
    """Capture handler invocations for ``db.get``/``db.restore``."""
    from osh.handlers import resolve as _real_resolve

    calls = {"get": [], "restore": []}

    def _fake_resolve(name):
        if name not in ("db.get", "db.restore"):
            return _real_resolve(name)
        record = calls[name.split(".", 1)[1]]

        class _RecordedOp:
            def __init__(self, ctx=None, **kwargs):
                record.append(kwargs)

            def run(self):
                pass

        return _RecordedOp

    monkeypatch.setattr("osh.handlers.resolve", _fake_resolve)
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
    from osh.cli_utils import handler_group
    from osh.plugins.osh_db_get.remotes import DbRemote

    remote = handler_group("remote", DbRemote)
    runner = CliRunner()
    runner.invoke(remote, ["add", "prod", "db://proddb"])

    cache_dir = in_project / ".osh" / "backups"
    cache_dir.mkdir(parents=True)
    prod = _write_cached_backup(cache_dir, "prod.dump", "db://proddb")

    result = runner.invoke(switch, ["env2", "--refresh=prod"])

    assert result.exit_code == 0, result.output
    assert patched_restore["restore"] == [(prod, patched_restore["db_name"], False)]
    assert patched_restore["created"] == [patched_restore["db_name"]]


# Multi-repository projects -------------------------------------------------


@pytest.fixture
def multi_repo_project(tmp_path, monkeypatch):
    """A project root without ``.git``, containing nested child repositories."""
    project = tmp_path / "project"
    (project / ".osh").mkdir(parents=True)
    (project / "not-a-repo").mkdir()
    for rel in ("odoo", "addons/custom"):
        repo = project / rel
        repo.mkdir(parents=True)
        _init_git(repo)
        subprocess.run(
            ["git", "switch", "-c", "19.0"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
    monkeypatch.chdir(project)
    return project


@requires_git
def test_find_project_repos_discovers_nested(multi_repo_project):
    """Repositories nested under non-repo directories are discovered."""
    assert find_project_repos(multi_repo_project) == [
        multi_repo_project / "addons" / "custom",
        multi_repo_project / "odoo",
    ]


def test_find_project_repos_empty(tmp_project):
    """A project with no valid repository reports an empty list."""
    # tmp_project's .git is an empty directory: not a usable repository.
    assert find_project_repos(tmp_project) == []


@requires_git
def test_get_current_branch_unanimous(multi_repo_project):
    """The shared branch is returned when all repositories agree."""
    assert get_current_branch(multi_repo_project) == "19.0"


@requires_git
def test_get_current_branch_diverged(multi_repo_project):
    """No branch is returned when repositories disagree."""
    subprocess.run(
        ["git", "switch", "-c", "fix"],
        cwd=multi_repo_project / "odoo",
        check=True,
        capture_output=True,
    )
    assert get_current_branch(multi_repo_project) is None


@requires_git
def test_switch_multi_repo_switches_all(multi_repo_project):
    """`osh switch -c <name>` creates the branch in every repository."""
    result = CliRunner().invoke(switch, ["-c", "feature/x"])

    assert result.exit_code == 0, result.output
    for rel in ("odoo", "addons/custom"):
        assert _git_current_branch(multi_repo_project / rel) == "feature/x"
    assert "Database: project-feature-x" in result.output


@requires_git
def test_switch_multi_repo_resolves_shared_branch_db(multi_repo_project):
    """The generated database name uses the shared multi-repo branch."""
    assert resolve_db_name(multi_repo_project) == "project-19-0"


@requires_git
def test_switch_multi_repo_records_active_env(multi_repo_project):
    """A multi-repo switch records the environment as a divergence fallback."""
    CliRunner().invoke(switch, ["-c", "feature/x"])
    assert get_active_env(multi_repo_project) == "feature/x"


@requires_git
def test_switch_multi_repo_create_only_where_missing(multi_repo_project):
    """`-c` keeps existing branches and creates the missing ones."""
    subprocess.run(
        ["git", "switch", "-c", "feature"],
        cwd=multi_repo_project / "odoo",
        check=True,
        capture_output=True,
    )

    result = CliRunner().invoke(switch, ["feature", "-c"])

    assert result.exit_code == 0, result.output
    assert _git_current_branch(multi_repo_project / "addons" / "custom") == "feature"


@requires_git
def test_switch_multi_repo_missing_branch_hint(multi_repo_project):
    """A missing branch reports the failed repositories and hints --create."""
    result = CliRunner().invoke(switch, ["does-not-exist"])

    assert result.exit_code != 0
    assert "Could not switch" in result.output
    assert "--create" in result.output


@requires_git
def test_switch_multi_repo_reports_each_branch(multi_repo_project):
    """`osh switch` without arguments lists each repository's branch."""
    result = CliRunner().invoke(switch, [])

    assert result.exit_code == 0, result.output
    assert "odoo: 19.0" in result.output
    assert f"{Path('addons') / 'custom'}: 19.0" in result.output
    assert "Database: project-19-0" in result.output


@requires_git
def test_switch_multi_repo_warns_on_diverged(multi_repo_project):
    """`osh switch` warns when repositories are on different branches."""
    subprocess.run(
        ["git", "switch", "-c", "fix"],
        cwd=multi_repo_project / "odoo",
        check=True,
        capture_output=True,
    )
    result = CliRunner().invoke(switch, [])

    assert result.exit_code == 0, result.output
    assert "different branches" in result.output


@requires_git
def test_switch_multi_repo_dry_run(multi_repo_project):
    """`osh switch --dry-run` prints commands without changing branches."""
    result = CliRunner().invoke(switch, ["other", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "Would run in odoo: git switch other" in result.output
    assert _git_current_branch(multi_repo_project / "odoo") == "19.0"
