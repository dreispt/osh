"""Tests for running osh inside a linked git worktree.

A linked worktree borrows its source repository's ``.osh`` environment
while keeping its own branch and addon directories: directories empty in
the worktree — typically uninitialized submodules — fall back to the
source checkout's copy.
"""

import shutil

import pytest
from click.testing import CliRunner

from osh.commands.db_cmd import db
from osh.commands.odoo_cmd import odoo
from osh.common import find_project_root
from osh.db import get_project_config, resolve_db_name, set_project_config
from osh.utils.odoo_layout import build_addons_paths

from .helpers import git, make_worktree_project

requires_git = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not available"
)


def _fake_odoo(project):
    """Install a stub ``odoo`` executable in the project's ``.venv``."""
    venv_bin = project / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    exe = venv_bin / "odoo"
    exe.write_text("#!/bin/sh\necho odoo 19.0")
    exe.chmod(0o755)


@requires_git
def test_commands_in_worktree_reuse_the_source_environment(tmp_path, monkeypatch):
    """``osh db set`` in a worktree writes the source repo's config.toml.

    A linked worktree is its own checkout — its branch names its own
    database — but the branch mappings live in the shared ``.osh`` of the
    source repository.
    """
    project, worktree = make_worktree_project(tmp_path)

    monkeypatch.chdir(worktree)
    result = CliRunner().invoke(db, ["set", "worktree-db"])

    assert result.exit_code == 0, result.output
    assert get_project_config(project, "db", "wt-feature") == "worktree-db"
    assert resolve_db_name(worktree) == "worktree-db"


@requires_git
def test_worktree_branch_names_its_own_database(tmp_path, monkeypatch):
    """``osh db show`` in a worktree resolves ``<project>-<worktree-branch>``.

    The generated name takes the project name from the source repository —
    the worktree directory name never leaks into database names — while the
    branch comes from the worktree itself.
    """
    project, worktree = make_worktree_project(tmp_path)

    monkeypatch.chdir(worktree)
    result = CliRunner().invoke(db, ["show"])

    assert result.exit_code == 0, result.output
    assert "Branch:   wt-feature" in result.output
    assert f"Database: {project.name}-wt-feature" in result.output


@requires_git
def test_worktree_addons_fall_back_to_source(tmp_path):
    """Uninitialized worktree submodules resolve to the source's copy.

    The worktree's own addon directories come first — its modules take
    precedence — and source directories whose worktree counterpart is
    missing or empty are appended after them.
    """
    project, worktree = make_worktree_project(tmp_path)

    paths = build_addons_paths(worktree)

    assert worktree in paths
    assert project / "sub" in paths
    assert paths.index(worktree) < paths.index(project / "sub")


@requires_git
def test_worktree_initialized_submodule_shadows_source(tmp_path):
    """An initialized worktree submodule shadows the source checkout's copy.

    Once ``git submodule update --init`` fills the worktree's ``sub``, the
    source repository's copy drops off the addons path — the worktree's
    branch is authoritative for what that directory contains.
    """
    project, worktree = make_worktree_project(tmp_path)
    git(
        worktree,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "update",
        "--init",
        "sub",
    )

    paths = build_addons_paths(worktree)

    assert worktree / "sub" in paths
    assert project / "sub" not in paths


@requires_git
def test_worktree_with_own_osh_is_its_own_environment(tmp_path, monkeypatch):
    """A worktree carrying its own ``.osh`` does not borrow the source's.

    ``osh init`` inside a worktree deliberately creates a separate
    environment: its config, database names and addon discovery all stay
    local to the worktree.
    """
    project, worktree = make_worktree_project(tmp_path)
    (worktree / ".osh").mkdir()

    monkeypatch.chdir(worktree)
    assert find_project_root() == worktree

    result = CliRunner().invoke(db, ["show"])
    assert result.exit_code == 0, result.output
    assert f"Database: {worktree.name}-wt-feature" in result.output
    assert get_project_config(project, "db", "wt-feature") is None


@requires_git
def test_osh_odoo_in_worktree_uses_shared_env_and_own_addons(tmp_path, monkeypatch):
    """``osh odoo`` in a worktree runs with the source env and local addons.

    The Odoo executable comes from the shared ``.venv``, the generated
    config lands in the source's ``.osh/cache/env``, and its addons_path
    lists the worktree's addon directories ahead of source fill-ins.
    """
    project, worktree = make_worktree_project(tmp_path)
    _fake_odoo(project)
    set_project_config(project, "db", "wt-feature", "worktree-db")

    monkeypatch.chdir(worktree)
    result = CliRunner().invoke(odoo, ["--dry-run"])

    assert result.exit_code == 0, result.output
    assert f"Using config: {project}/.osh/cache/env/" in result.output
    assert "Using database: worktree-db" in result.output

    conf_line = next(line for line in result.output.splitlines() if "cache/env" in line)
    conf = project / ".osh" / "cache" / "env" / conf_line.rsplit("/", 1)[1]
    addons_path = next(
        line for line in conf.read_text().splitlines() if "addons_path" in line
    )
    assert str(worktree) in addons_path
    assert str(project / "sub") in addons_path
    assert addons_path.index(str(worktree)) < addons_path.index(str(project / "sub"))
