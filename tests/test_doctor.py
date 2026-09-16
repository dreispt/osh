"""Tests for ``osh doctor``."""

import pytest
from click.testing import CliRunner

from osh.backends import NoneBackend
from osh.cli import main
from osh.db import set_project_config


class TestDoctorVersionReporting:
    def test_local_diagnose_reports_installed_odoo_version(
        self, tmp_project, fake_odoo_executable
    ):
        """``osh doctor`` (via NoneBackend.diagnose) reports the installed Odoo version."""
        backend = NoneBackend()
        diagnostics = backend.diagnose(tmp_project)
        assert diagnostics.info["none"]["odoo_version"] == "odoo 19.0"

    def test_doctor_reports_installed_odoo_version(
        self, tmp_project, fake_odoo_executable, monkeypatch
    ):
        """``osh doctor`` prints the installed Odoo version for the active target."""
        set_project_config(tmp_project, "init", "target", "none")
        monkeypatch.chdir(tmp_project)
        runner = CliRunner()
        result = runner.invoke(main, ["doctor"])
        assert result.exit_code == 0, result.output
        assert "Odoo version: odoo 19.0" in result.output


class TestDoctorNesting:
    def test_doctor_warns_on_unacknowledged_enclosing_project(
        self, tmp_path, monkeypatch
    ):
        """A nested project without a recorded parent is reported."""
        parent = tmp_path / "parent"
        (parent / ".osh").mkdir(parents=True)
        child = parent / "child"
        (child / ".osh").mkdir(parents=True)
        (child / ".git").mkdir()
        monkeypatch.chdir(child)

        result = CliRunner().invoke(main, ["doctor"])

        assert result.exit_code == 0, result.output
        assert "nested inside the Osh project" in result.output
        assert str(parent.resolve()) in result.output

    @pytest.mark.parametrize(
        "recorded",
        [
            "..",  # written by current ``osh init``
            str,  # legacy absolute path; resolved against the parent below
        ],
    )
    def test_doctor_shows_acknowledged_parent_as_info(
        self, tmp_path, monkeypatch, recorded
    ):
        """An intentional nested project lists its parent, without warning."""
        parent = tmp_path / "parent"
        (parent / ".osh").mkdir(parents=True)
        child = parent / "child"
        (child / ".osh").mkdir(parents=True)
        (child / ".git").mkdir()
        value = recorded if recorded == ".." else str(parent.resolve())
        set_project_config(child, "init", "parent", value)
        monkeypatch.chdir(child)

        result = CliRunner().invoke(main, ["doctor"])

        assert result.exit_code == 0, result.output
        assert "nested inside the Osh project" not in result.output
        assert str(parent.resolve()) in result.output

    def test_doctor_warns_on_nested_projects_below(self, tmp_path, monkeypatch):
        """A workspace project reports nested ``.osh`` environments below it."""
        (tmp_path / ".osh").mkdir()
        (tmp_path / "child" / ".osh").mkdir(parents=True)
        monkeypatch.chdir(tmp_path)

        result = CliRunner().invoke(main, ["doctor"])

        assert result.exit_code == 0, result.output
        assert "Nested Osh project at 'child'" in result.output

    def test_doctor_ignores_osh_below_git_repo(self, tmp_path, monkeypatch):
        """A ``.osh`` below a repository root is never used; no warning."""
        (tmp_path / ".osh").mkdir()
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "HEAD").touch()
        (tmp_path / "sub" / ".osh").mkdir(parents=True)
        monkeypatch.chdir(tmp_path)

        result = CliRunner().invoke(main, ["doctor"])

        assert result.exit_code == 0, result.output
        assert "Nested Osh project" not in result.output
