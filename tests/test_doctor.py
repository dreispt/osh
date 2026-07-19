"""Tests for ``osh doctor``."""

from click.testing import CliRunner

from osh.cli import main
from osh.db import set_project_config
from osh.plugins.osh_local.backends import LocalBackend


class TestDoctorVersionReporting:
    def test_local_diagnose_reports_installed_odoo_version(
        self, tmp_project, fake_odoo_executable
    ):
        """``osh doctor`` (via LocalBackend.diagnose) reports the installed Odoo version."""
        backend = LocalBackend()
        diagnostics = backend.diagnose(tmp_project)
        assert diagnostics.info["odoo_version"] == "odoo 19.0"

    def test_doctor_reports_installed_odoo_version(
        self, tmp_project, fake_odoo_executable, monkeypatch
    ):
        """``osh doctor`` prints the installed Odoo version for the active target."""
        set_project_config(tmp_project, "init", "target", "local")
        monkeypatch.chdir(tmp_project)
        runner = CliRunner()
        result = runner.invoke(main, ["doctor"])
        assert result.exit_code == 0, result.output
        assert "Odoo version: odoo 19.0" in result.output


class TestDoctorBackendListing:
    def test_doctor_lists_installed_backends_and_options(
        self, tmp_project, monkeypatch
    ):
        """``osh doctor`` reports the installed backends and their init options."""
        monkeypatch.chdir(tmp_project)
        runner = CliRunner()
        result = runner.invoke(main, ["doctor"])
        assert result.exit_code == 0, result.output
        assert "Installed backends:" in result.output
        assert "local" in result.output
        assert "docker" in result.output
        assert "--odoo-source" in result.output
        assert "--service" in result.output
