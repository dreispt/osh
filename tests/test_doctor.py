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
        assert diagnostics.info["Project"]["odoo_version"] == "odoo 19.0"

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
