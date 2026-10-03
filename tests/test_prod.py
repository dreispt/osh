"""Tests for production/sysadmin support: ``osh init --prod``, ``ODOO_RC``,
``OSH_PROJECT_DIR``, production confirmations and the ``host`` runtime."""

import click
import pytest
from click.testing import CliRunner

from osh.cli import main
from osh.commands.init_cmd import _parse_odoo_version
from osh.common import find_project_root, get_osh_odoo_config_path
from osh.db import get_project_config, set_project_config


def _osh_conf(project):
    return project / ".osh" / "odoo.conf"


class TestInitProd:
    """``osh init --prod`` sets up a project on an existing environment."""

    def test_prod_detects_version_and_records_prod(
        self, tmp_project, fake_odoo_executable
    ):
        """The version comes from ``odoo --version``; ``init.prod`` is recorded."""
        result = CliRunner().invoke(
            main, ["init", "--prod", "--edition", "ce", str(tmp_project)]
        )

        assert result.exit_code == 0, result.output
        assert "Detected Odoo 19.0" in result.output
        assert get_project_config(tmp_project, "init", "version") == "19.0"
        assert get_project_config(tmp_project, "init", "prod") is True
        assert get_project_config(tmp_project, "init", "dev") is False

    def test_prod_does_not_write_dev_limits(self, tmp_project, fake_odoo_executable):
        """``--prod`` never disables the CPU/real-time limits."""
        (tmp_project / ".odoorc").write_text("[options]\nlimit_time_cpu = 60\n")

        result = CliRunner().invoke(
            main, ["init", "--prod", "--edition", "ce", str(tmp_project)]
        )

        assert result.exit_code == 0, result.output
        conf = _osh_conf(tmp_project).read_text()
        assert "limit_time_cpu = 60" in conf
        assert "limit_time_real" not in conf

    def test_dev_init_still_writes_dev_limits(self, tmp_project):
        """Without ``--prod`` the dev-friendly limits are still applied."""
        result = CliRunner().invoke(
            main, ["init", "19.0", "--edition", "ce", str(tmp_project)]
        )

        assert result.exit_code == 0, result.output
        assert "limit_time_cpu = 0" in _osh_conf(tmp_project).read_text()
        assert get_project_config(tmp_project, "init", "prod") is None

    def test_prod_skips_git_warning_prompt(self, tmp_path, fake_odoo_executable):
        """``--prod`` implies ``--yes``: no prompt outside a git repository."""
        target = tmp_path / "server"
        target.mkdir()

        # No input: any prompt would hit EOF and abort.
        result = CliRunner().invoke(main, ["init", "--prod", "19.0", str(target)])

        assert result.exit_code == 0, result.output
        assert get_project_config(target, "init", "prod") is True

    def test_explicit_version_wins_over_detection(
        self, tmp_project, fake_odoo_executable
    ):
        """An explicit VERSION skips executable detection."""
        result = CliRunner().invoke(
            main, ["init", "--prod", "18.0", "--edition", "ce", str(tmp_project)]
        )

        assert result.exit_code == 0, result.output
        assert "Detected Odoo" not in result.output
        assert get_project_config(tmp_project, "init", "version") == "18.0"

    def test_reinit_keeps_prod_mode(self, tmp_project, fake_odoo_executable):
        """A bare ``osh init`` on a production project keeps production mode."""
        set_project_config(tmp_project, "init", "prod", True)

        result = CliRunner().invoke(main, ["init", "--edition", "ce", str(tmp_project)])

        assert result.exit_code == 0, result.output
        assert get_project_config(tmp_project, "init", "prod") is True
        assert not _osh_conf(tmp_project).exists() or (
            "limit_time_cpu = 0" not in _osh_conf(tmp_project).read_text()
        )

    def test_prod_removes_previous_dev_limits(self, tmp_project, fake_odoo_executable):
        """Re-initialising a dev project with ``--prod`` drops zero limits."""
        _osh_conf(tmp_project).write_text(
            "[options]\nlimit_time_cpu = 0\nlimit_time_real = 0\nworkers = 2\n"
        )

        result = CliRunner().invoke(
            main, ["init", "--prod", "--edition", "ce", str(tmp_project)]
        )

        assert result.exit_code == 0, result.output
        conf = _osh_conf(tmp_project).read_text()
        assert "limit_time" not in conf
        assert "workers = 2" in conf

    def test_prod_switches_to_host_runtime(self, tmp_project, fake_odoo_executable):
        """``--prod`` makes ``host`` the active runtime."""
        set_project_config(tmp_project, "run", "runtime", "docker")

        result = CliRunner().invoke(
            main, ["init", "--prod", "--edition", "ce", str(tmp_project)]
        )

        assert result.exit_code == 0, result.output
        assert get_project_config(tmp_project, "run", "runtime") == "host"

    def test_prod_disables_dev_default(self, tmp_project):
        """``osh odoo`` injects no ``--dev`` default on production projects."""
        from osh.commands.odoo_cmd import _resolve_dev_default

        assert _resolve_dev_default(tmp_project, no_dev=False) == "all"
        set_project_config(tmp_project, "init", "prod", True)
        assert _resolve_dev_default(tmp_project, no_dev=False) is None
        # An explicit project setting still wins.
        set_project_config(tmp_project, "odoo", "dev", "xml")
        assert _resolve_dev_default(tmp_project, no_dev=False) == "xml"


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Odoo Server 17.0", "17.0"),
        ("Odoo Server 16.0+e", "16.0"),
        ("Odoo Server saas~17.4", "saas-17.4"),
        ("odoo 19.0", "19.0"),
        ("garbage", None),
    ],
)
def test_parse_odoo_version(text, expected):
    """``odoo --version`` output maps to an Osh version string."""
    assert _parse_odoo_version(text) == expected


class TestExternalOdooRc:
    """An ``ODOO_RC`` file is used in place instead of copying ``.odoorc``."""

    def test_init_uses_odoo_rc_in_place(
        self, tmp_project, tmp_path, monkeypatch, fake_odoo_executable
    ):
        """The external config is recorded and left untouched."""
        etc_conf = tmp_path / "etc" / "odoo.conf"
        etc_conf.parent.mkdir()
        etc_conf.write_text("[options]\ndb_name = live\n")
        (tmp_project / ".odoorc").write_text("[options]\ndb_name = dev\n")
        monkeypatch.setenv("ODOO_RC", str(etc_conf))

        result = CliRunner().invoke(
            main, ["init", "--prod", "--edition", "ce", str(tmp_project)]
        )

        assert result.exit_code == 0, result.output
        assert get_project_config(tmp_project, "init", "odoo_rc") == str(etc_conf)
        assert not _osh_conf(tmp_project).exists()
        assert get_osh_odoo_config_path(tmp_project) == etc_conf
        assert etc_conf.read_text() == "[options]\ndb_name = live\n"

    def test_dev_init_does_not_modify_odoo_rc(self, tmp_project, tmp_path, monkeypatch):
        """Dev options are never written into an external config."""
        etc_conf = tmp_path / "odoo.conf"
        etc_conf.write_text("[options]\n")
        monkeypatch.setenv("ODOO_RC", str(etc_conf))

        result = CliRunner().invoke(
            main, ["init", "19.0", "--edition", "ce", str(tmp_project)]
        )

        assert result.exit_code == 0, result.output
        assert etc_conf.read_text() == "[options]\n"

    def test_reinit_without_env_keeps_recorded_odoo_rc(
        self, tmp_project, tmp_path, monkeypatch, fake_odoo_executable
    ):
        """A later init without ``ODOO_RC`` keeps using the recorded file."""
        etc_conf = tmp_path / "odoo.conf"
        etc_conf.write_text("[options]\n")
        (tmp_project / ".odoorc").write_text("[options]\ndb_name = dev\n")
        monkeypatch.setenv("ODOO_RC", str(etc_conf))
        runner = CliRunner()
        args = ["init", "--prod", "--edition", "ce", str(tmp_project)]
        assert runner.invoke(main, args).exit_code == 0
        monkeypatch.delenv("ODOO_RC")

        result = runner.invoke(main, args)

        assert result.exit_code == 0, result.output
        assert not _osh_conf(tmp_project).exists()
        assert get_osh_odoo_config_path(tmp_project) == etc_conf

    def test_data_dir_follows_active_config(self, tmp_project, tmp_path):
        """``get_odoo_data_dir`` reads the external config only when it is active."""
        from osh.common import get_odoo_data_dir

        etc_conf = tmp_path / "odoo.conf"
        etc_conf.write_text(f"[options]\ndata_dir = {tmp_path / 'external'}\n")
        set_project_config(tmp_project, "init", "odoo_rc", str(etc_conf))
        assert get_odoo_data_dir(tmp_project) == tmp_path / "external"

        _osh_conf(tmp_project).write_text("[options]\n")
        (tmp_project / ".odoorc").write_text(
            f"[options]\ndata_dir = {tmp_path / 'local'}\n"
        )
        assert get_odoo_data_dir(tmp_project) == tmp_path / "local"

    def test_missing_odoo_rc_falls_back_to_copy(
        self, tmp_project, tmp_path, monkeypatch
    ):
        """A dangling ``ODOO_RC`` is ignored; ``.odoorc`` is copied as before."""
        (tmp_project / ".odoorc").write_text("[options]\ndb_name = dev\n")
        monkeypatch.setenv("ODOO_RC", str(tmp_path / "missing.conf"))

        result = CliRunner().invoke(
            main, ["init", "19.0", "--edition", "ce", str(tmp_project)]
        )

        assert result.exit_code == 0, result.output
        assert "db_name = dev" in _osh_conf(tmp_project).read_text()
        assert get_project_config(tmp_project, "init", "odoo_rc") is None

    def test_local_osh_conf_wins(self, tmp_project, tmp_path, monkeypatch):
        """An existing ``.osh/odoo.conf`` is kept over ``ODOO_RC``."""
        _osh_conf(tmp_project).write_text("[options]\n")
        etc_conf = tmp_path / "odoo.conf"
        etc_conf.write_text("[options]\n")
        monkeypatch.setenv("ODOO_RC", str(etc_conf))

        result = CliRunner().invoke(
            main, ["init", "19.0", "--edition", "ce", str(tmp_project)]
        )

        assert result.exit_code == 0, result.output
        assert get_project_config(tmp_project, "init", "odoo_rc") is None
        assert get_osh_odoo_config_path(tmp_project) == _osh_conf(tmp_project)


class TestProjectDirEnv:
    """``OSH_PROJECT_DIR`` locates the project regardless of cwd."""

    def test_find_project_root_uses_env(self, tmp_project, tmp_path, monkeypatch):
        """The env var wins over walking up from the current directory."""
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        monkeypatch.setenv("OSH_PROJECT_DIR", str(tmp_project))

        assert find_project_root() == tmp_project.resolve()

    def test_relative_env_is_resolved(self, tmp_project, monkeypatch):
        """A relative ``OSH_PROJECT_DIR`` is resolved to an absolute path."""
        monkeypatch.chdir(tmp_project.parent)
        monkeypatch.setenv("OSH_PROJECT_DIR", tmp_project.name)

        root = find_project_root()
        assert root.is_absolute() and root == tmp_project.resolve()

    def test_invalid_env_fails_when_required(self, tmp_path, monkeypatch):
        """A path without ``.osh`` is reported clearly."""
        monkeypatch.setenv("OSH_PROJECT_DIR", str(tmp_path))

        assert find_project_root() is None
        with pytest.raises(click.ClickException, match="OSH_PROJECT_DIR"):
            find_project_root(required=True)

    def test_command_runs_outside_project(self, tmp_project, tmp_path, monkeypatch):
        """``osh runtime status`` works from anywhere with the env var set."""
        set_project_config(tmp_project, "run", "runtime", "venv")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("OSH_PROJECT_DIR", str(tmp_project))

        result = CliRunner().invoke(main, ["runtime", "status"])

        assert result.exit_code == 0, result.output
        assert "Active runtime: venv" in result.output

    def test_init_defaults_to_env_dir(self, tmp_path, monkeypatch):
        """``osh init`` without DIRECTORY initialises ``OSH_PROJECT_DIR``."""
        target = tmp_path / "srv"
        target.mkdir()
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("OSH_PROJECT_DIR", str(target))

        result = CliRunner().invoke(main, ["init", "19.0", "--edition", "ce", "--yes"])

        assert result.exit_code == 0, result.output
        assert (target / ".osh").is_dir()
        assert not (tmp_path / ".osh").exists()


class TestProdDrop:
    """``osh db drop`` asks for the database name on production projects."""

    @pytest.fixture
    def drop(self):
        from osh.cli_utils import handler_command
        from osh.plugins.osh_db_drop.drop_cmd import DbDrop

        return handler_command("drop", DbDrop)

    def test_wrong_name_aborts_even_with_force(
        self, drop, tmp_project, pg_db, monkeypatch
    ):
        """``--force`` does not bypass the typed confirmation."""
        set_project_config(tmp_project, "init", "prod", True)
        name = pg_db.create()
        monkeypatch.chdir(tmp_project)

        result = CliRunner().invoke(drop, [name, "--force"], input="nope\n")

        assert result.exit_code != 0
        assert "did not match" in result.output
        assert pg_db.exists(name)

    def test_typed_name_drops(self, drop, tmp_project, pg_db, monkeypatch):
        """Typing the exact database name drops it."""
        set_project_config(tmp_project, "init", "prod", True)
        name = pg_db.create()
        monkeypatch.chdir(tmp_project)

        result = CliRunner().invoke(drop, [name], input=f"{name}\n")

        assert result.exit_code == 0, result.output
        assert "production project" in result.output
        assert not pg_db.exists(name)


class TestProdRestore:
    """``osh backup restore`` asks for the database name on production projects."""

    @pytest.fixture
    def restore(self, in_project):
        from osh.cli_utils import handler_command
        from osh.plugins.osh_backup.restore_cmd import DbRestore

        cache_dir = in_project / ".osh" / "backups"
        cache_dir.mkdir(parents=True)
        (cache_dir / "b.dump").write_bytes(b"x")
        set_project_config(in_project, "init", "prod", True)
        return handler_command("restore", DbRestore)

    def test_wrong_name_aborts(self, restore, patched_restore):
        """A mismatched name restores and neutralizes nothing."""
        result = CliRunner().invoke(restore, ["--force"], input="nope\n")

        assert result.exit_code != 0
        assert "did not match" in result.output
        assert patched_restore["restore"] == []
        assert patched_restore["dropped"] == []
        assert patched_restore["neutralize"] == []

    def test_typed_name_restores(self, restore, patched_restore):
        """Typing the target database name proceeds with the restore."""
        db_name = patched_restore["db_name"]

        result = CliRunner().invoke(restore, [], input=f"{db_name}\n")

        assert result.exit_code == 0, result.output
        assert "restore and neutralize" in result.output
        assert len(patched_restore["restore"]) == 1


class TestHostRuntime:
    """The built-in ``host`` runtime and its legacy ``none`` name."""

    def test_none_backend_alias(self):
        """``NoneBackend`` remains importable and is the host runtime."""
        from osh.backends import HostBackend, NoneBackend

        assert NoneBackend is HostBackend
        assert HostBackend.name == "host"
        assert HostBackend.label == "Host"

    def test_get_backend_class_resolves_legacy_names(self):
        """``none`` (and ``local``) resolve to ``HostBackend``."""
        from osh.backends import HostBackend
        from osh.utils.plugin_loader import get_backend_class

        for name in ("host", "none", "local"):
            assert get_backend_class(name) is HostBackend

    def test_resolve_backend_with_legacy_run_target(self, tmp_project):
        """A project recorded with ``run.target = none`` runs on the host."""
        from osh.backends import HostBackend
        from osh.db import resolve_backend

        set_project_config(tmp_project, "run", "target", "none")

        assert isinstance(resolve_backend(tmp_project), HostBackend)
