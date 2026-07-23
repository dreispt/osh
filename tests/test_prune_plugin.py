"""Tests for ``osh prune`` plugin."""

from click.testing import CliRunner

from osh.plugins.osh_local.commands import prune


def test_prune_outside_project_fails(tmp_path, monkeypatch):
    """``osh prune`` fails when not inside an Osh project."""
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    result = runner.invoke(prune, [])

    assert result.exit_code == 0
    assert "Not inside an Osh project" in result.output


def test_prune_runs_git_gc_on_clones(in_project, subprocess_run_capture):
    """Prune runs ``git gc`` on all local source clones."""
    osh_dir = in_project / ".osh"
    sources = ["odoo", "enterprise", "design-themes"]
    for name in sources:
        (osh_dir / name / ".git").mkdir(parents=True, exist_ok=True)

    calls = subprocess_run_capture.calls

    runner = CliRunner()
    result = runner.invoke(prune, [])

    assert result.exit_code == 0
    assert len(calls) == 3
    for name, call in zip(sources, calls):
        assert call[:3] == ["git", "-C", str(osh_dir / name)]
        assert "gc" in call
    assert "Pruned 3 source clone(s)" in result.output


def test_prune_skips_symlinked_sources(in_project, subprocess_run_capture):
    """Prune does not run on symlinked sources."""
    osh_dir = in_project / ".osh"
    osh_dir.mkdir(parents=True, exist_ok=True)
    external = in_project / "external-odoo"
    external.mkdir(parents=True, exist_ok=True)
    (external / ".git").mkdir(parents=True, exist_ok=True)
    (osh_dir / "odoo").symlink_to(external, target_is_directory=True)

    calls = subprocess_run_capture.calls

    runner = CliRunner()
    result = runner.invoke(prune, [])

    assert result.exit_code == 0
    assert len(calls) == 0
    assert "symlinked source" in result.output


def test_prune_aggressive_option(in_project, subprocess_run_capture):
    """``--aggressive`` passes the flag to ``git gc``."""
    osh_dir = in_project / ".osh"
    (osh_dir / "odoo" / ".git").mkdir(parents=True, exist_ok=True)

    calls = subprocess_run_capture.calls

    runner = CliRunner()
    result = runner.invoke(prune, ["--aggressive"])

    assert result.exit_code == 0
    assert len(calls) == 1
    assert "--aggressive" in calls[0]


def test_prune_dry_run_does_not_call(in_project, subprocess_run_capture):
    """``--dry-run`` prints commands without executing them."""
    osh_dir = in_project / ".osh"
    (osh_dir / "odoo" / ".git").mkdir(parents=True, exist_ok=True)

    calls = subprocess_run_capture.calls

    runner = CliRunner()
    result = runner.invoke(prune, ["--dry-run"])

    assert result.exit_code == 0
    assert len(calls) == 0
    assert "Would run:" in result.output
    assert "git" in result.output


def test_prune_reports_missing_git_executable(in_project, monkeypatch):
    """A missing git executable is reported clearly."""
    osh_dir = in_project / ".osh"
    (osh_dir / "odoo" / ".git").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        "osh.plugins.osh_local.commands.run_subprocess",
        lambda *a, **kw: (None, "", ""),
    )

    runner = CliRunner()
    result = runner.invoke(prune, [])

    assert result.exit_code != 0
    assert "Could not locate git executable" in result.output


def test_prune_reports_git_failure(in_project, monkeypatch):
    """A failing ``git gc`` is reported as a ClickException."""
    osh_dir = in_project / ".osh"
    (osh_dir / "odoo" / ".git").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        "osh.plugins.osh_local.commands.run_subprocess",
        lambda *a, **kw: (1, "", ""),
    )

    runner = CliRunner()
    result = runner.invoke(prune, [])

    assert result.exit_code != 0
    assert "Failed to prune odoo" in result.output
