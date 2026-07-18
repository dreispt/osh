"""Tests for ``osh prune`` plugin."""

from __future__ import annotations

import subprocess as subprocess_module
from pathlib import Path

from click.testing import CliRunner

from osh.plugins.osh_local.commands import prune


def test_prune_outside_project_fails(tmp_path: Path, monkeypatch) -> None:
    """``osh prune`` fails when not inside an Osh project."""
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(prune, [])

    assert result.exit_code == 0
    assert "Not inside an Osh project" in result.output


def test_prune_runs_git_gc_on_clones(
    in_project: Path, subprocess_check_call_capture: list[list[str]]
) -> None:
    """Prune runs ``git gc`` on all local source clones."""
    osh_dir = in_project / ".osh"
    sources = ["odoo", "enterprise", "design-themes"]
    for name in sources:
        (osh_dir / name / ".git").mkdir(parents=True, exist_ok=True)

    calls = subprocess_check_call_capture

    runner = CliRunner()
    result = runner.invoke(prune, [])

    assert result.exit_code == 0
    assert len(calls) == 3
    for name, call in zip(sources, calls):
        assert call[:3] == ["git", "-C", str(osh_dir / name)]
        assert "gc" in call
    assert "Pruned 3 source clone(s)" in result.output


def test_prune_skips_symlinked_sources(
    in_project: Path, subprocess_check_call_capture: list[list[str]]
) -> None:
    """Prune does not run on symlinked sources."""
    osh_dir = in_project / ".osh"
    osh_dir.mkdir(parents=True, exist_ok=True)
    external = in_project / "external-odoo"
    external.mkdir(parents=True, exist_ok=True)
    (external / ".git").mkdir(parents=True, exist_ok=True)
    (osh_dir / "odoo").symlink_to(external, target_is_directory=True)

    calls = subprocess_check_call_capture

    runner = CliRunner()
    result = runner.invoke(prune, [])

    assert result.exit_code == 0
    assert len(calls) == 0
    assert "symlinked source" in result.output


def test_prune_aggressive_option(
    in_project: Path, subprocess_check_call_capture: list[list[str]]
) -> None:
    """``--aggressive`` passes the flag to ``git gc``."""
    osh_dir = in_project / ".osh"
    (osh_dir / "odoo" / ".git").mkdir(parents=True, exist_ok=True)

    calls = subprocess_check_call_capture

    runner = CliRunner()
    result = runner.invoke(prune, ["--aggressive"])

    assert result.exit_code == 0
    assert len(calls) == 1
    assert "--aggressive" in calls[0]


def test_prune_dry_run_does_not_call(
    in_project: Path, subprocess_check_call_capture: list[list[str]]
) -> None:
    """``--dry-run`` prints commands without executing them."""
    osh_dir = in_project / ".osh"
    (osh_dir / "odoo" / ".git").mkdir(parents=True, exist_ok=True)

    calls = subprocess_check_call_capture

    runner = CliRunner()
    result = runner.invoke(prune, ["--dry-run"])

    assert result.exit_code == 0
    assert len(calls) == 0
    assert "Would run:" in result.output
    assert "git" in result.output


def test_prune_reports_missing_git_executable(in_project: Path, monkeypatch) -> None:
    """A missing git executable is reported clearly."""
    osh_dir = in_project / ".osh"
    (osh_dir / "odoo" / ".git").mkdir(parents=True, exist_ok=True)

    def raise_file_not_found(*args, **kwargs) -> None:
        raise FileNotFoundError()

    monkeypatch.setattr(
        "osh.plugins.osh_local.commands.subprocess.check_call",
        raise_file_not_found,
    )

    runner = CliRunner()
    result = runner.invoke(prune, [])

    assert result.exit_code != 0
    assert "Could not locate git executable" in result.output


def test_prune_reports_git_failure(in_project: Path, monkeypatch) -> None:
    """A failing ``git gc`` is reported as a ClickException."""
    osh_dir = in_project / ".osh"
    (osh_dir / "odoo" / ".git").mkdir(parents=True, exist_ok=True)

    def raise_called_process_error(*args, **kwargs) -> None:
        raise subprocess_module.CalledProcessError(1, "git gc")

    monkeypatch.setattr(
        "osh.plugins.osh_local.commands.subprocess.check_call",
        raise_called_process_error,
    )

    runner = CliRunner()
    result = runner.invoke(prune, [])

    assert result.exit_code != 0
    assert "Failed to prune odoo" in result.output
