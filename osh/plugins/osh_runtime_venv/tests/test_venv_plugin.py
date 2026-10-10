"""Tests for the built-in venv runtime plugin."""

import sys
from pathlib import Path

from click.testing import CliRunner

from osh.cli import main
from osh.db import get_project_config, set_project_config
from osh.plugins.osh_runtime_venv.runtimes import VenvRuntime
from tests.helpers import make_bare_repo

from .conftest import real_commands


def test_diagnose_quiet_until_requirements_change_after_init(tmp_project, monkeypatch):
    """Editing requirements.txt after init prompts an ``osh init`` hint.

    Init fingerprints the requirement files it installed from; the check
    is silent until one of them changes.
    """
    odoo_src = tmp_project / "odoo"
    odoo_src.mkdir()
    (odoo_src / "odoo-bin").touch()
    requirements = tmp_project / "requirements.txt"
    requirements.write_text("requests\n")
    real_commands(monkeypatch)
    result = CliRunner().invoke(
        main,
        ["init", "19.0", str(tmp_project), "--runtime", "venv", "-c", str(odoo_src)],
    )
    assert result.exit_code == 0

    runtime = VenvRuntime()
    warnings = runtime.diagnose(tmp_project, phase="run").warnings
    assert not any("osh init venv" in w for w in warnings)

    requirements.write_text("requests\nhttpx\n")
    warnings = runtime.diagnose(tmp_project, phase="run").warnings
    assert any("osh init venv" in w for w in warnings)


def test_init_fingerprint_accepts_inputs_without_reinstall(tmp_project, monkeypatch):
    """``osh init --fingerprint`` re-fingerprints inputs without reinstalling.

    A requirements edit after init flags the environment as stale; a bare
    ``--fingerprint`` targets the project's active runtime and accepts the
    inputs as built — no pip installs run and the warning clears.
    """
    odoo_src = tmp_project / "odoo"
    odoo_src.mkdir()
    (odoo_src / "odoo-bin").touch()
    requirements = tmp_project / "requirements.txt"
    requirements.write_text("requests\n")
    calls = real_commands(monkeypatch)
    init = [
        "init",
        "19.0",
        str(tmp_project),
        "--runtime",
        "venv",
        "-c",
        str(odoo_src),
    ]
    result = CliRunner().invoke(main, init)
    assert result.exit_code == 0

    requirements.write_text("requests\nhttpx\n")
    warnings = VenvRuntime().diagnose(tmp_project, phase="run").warnings
    assert any("osh init venv" in w for w in warnings)

    calls.clear()
    result = CliRunner().invoke(main, ["init", str(tmp_project), "--fingerprint"])
    assert result.exit_code == 0
    assert not any("install" in call for call in calls)

    warnings = VenvRuntime().diagnose(tmp_project, phase="run").warnings
    assert not any("osh init venv" in w for w in warnings)


class TestInitCommand:
    def test_with_project_sources(self, tmp_project, monkeypatch):
        odoo_src = tmp_project / "odoo"
        odoo_src.mkdir(parents=True, exist_ok=True)
        (odoo_src / "odoo-bin").touch()
        web = tmp_project / "enterprise" / "web"
        web.mkdir(parents=True, exist_ok=True)
        (web / "__manifest__.py").touch()

        real_commands(monkeypatch)
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["init", "19.0", str(tmp_project), "--runtime", "venv", "--edition", "ee"],
        )

        assert result.exit_code == 0
        assert (tmp_project / ".osh" / "odoo").is_symlink()
        assert (tmp_project / ".osh" / "enterprise").is_symlink()
        assert (tmp_project / ".osh" / "config.toml").exists()

    def test_with_source_flags(self, tmp_project, monkeypatch):
        odoo_src = tmp_project / "my-odoo"
        odoo_src.mkdir(parents=True, exist_ok=True)
        (odoo_src / "odoo-bin").touch()
        ent_src = tmp_project / "my-ent"
        web = ent_src / "web"
        web.mkdir(parents=True, exist_ok=True)
        (web / "__manifest__.py").touch()

        real_commands(monkeypatch)
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "init",
                "19.0",
                str(tmp_project),
                "--runtime",
                "venv",
                "-c",
                str(odoo_src),
                "-e",
                str(ent_src),
            ],
        )

        assert result.exit_code == 0
        assert (tmp_project / ".osh" / "odoo").resolve() == odoo_src.resolve()
        assert (tmp_project / ".osh" / "enterprise").resolve() == ent_src.resolve()

    def test_with_themes_source_flag(self, tmp_project, monkeypatch):
        odoo_src = tmp_project / "my-odoo"
        odoo_src.mkdir(parents=True, exist_ok=True)
        (odoo_src / "odoo-bin").touch()
        themes_src = tmp_project / "my-themes"
        theme = themes_src / "theme_buzzy"
        theme.mkdir(parents=True, exist_ok=True)
        (theme / "__manifest__.py").touch()

        real_commands(monkeypatch)
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "init",
                "19.0",
                str(tmp_project),
                "--runtime",
                "venv",
                "-c",
                str(odoo_src),
                "--themes-source",
                str(themes_src),
            ],
        )

        assert result.exit_code == 0
        assert (tmp_project / ".osh" / "odoo").resolve() == odoo_src.resolve()
        assert (
            tmp_project / ".osh" / "design-themes"
        ).resolve() == themes_src.resolve()

    def test_cache_first_non_interactive(
        self,
        tmp_path,
        tmp_project,
        patch_cache,
        monkeypatch,
    ):
        odoo_bare = make_bare_repo(tmp_path, "odoo")
        ent_bare = make_bare_repo(tmp_path, "enterprise")
        themes_bare = make_bare_repo(tmp_path, "design-themes")
        monkeypatch.setattr("osh.sources.DEFAULT_ODOO_URL", f"file://{odoo_bare}")
        monkeypatch.setattr("osh.sources.DEFAULT_ENTERPRISE_URL", f"file://{ent_bare}")
        monkeypatch.setattr("osh.sources.DEFAULT_THEMES_URL", f"file://{themes_bare}")
        real_commands(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(
            main, ["init", "master", str(tmp_project), "--runtime", "venv", "--sh"]
        )

        assert result.exit_code == 0
        assert (patch_cache / "odoo.git").exists()
        assert (patch_cache / "enterprise.git").exists()
        assert (patch_cache / "design-themes.git").exists()
        assert (tmp_project / ".osh" / "odoo" / ".git").is_dir()
        assert (tmp_project / ".osh" / "enterprise" / ".git").is_dir()
        assert (tmp_project / ".osh" / "design-themes" / ".git").is_dir()

    def test_pip_install_failure_still_initializes(self, tmp_project, monkeypatch):
        """If pip install fails, the project environment is still created."""
        odoo_src = tmp_project / "odoo"
        odoo_src.mkdir(parents=True, exist_ok=True)
        (odoo_src / "odoo-bin").touch()
        (odoo_src / "requirements.txt").touch()
        ent_src = tmp_project / "enterprise"
        web = ent_src / "web"
        web.mkdir(parents=True, exist_ok=True)
        (web / "__manifest__.py").touch()

        monkeypatch.setattr("venv.create", lambda *a, **kw: None)

        monkeypatch.setattr(
            "osh.plugins.osh_runtime_venv.utils.run_subprocess",
            lambda *args, **kwargs: (1, "", ""),
        )

        # Force the use of the running interpreter so venv.create is used.
        current_version = f"{sys.version_info.major}.{sys.version_info.minor}"
        monkeypatch.setattr(
            "osh.plugins.osh_runtime_venv.utils.resolve_python_for_odoo",
            lambda version: {
                "exe": Path(sys.executable),
                "version": current_version,
                "recommended": "3.12",
                "supported": ["3.12"],
            },
        )

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "init",
                "19.0",
                str(tmp_project),
                "--runtime",
                "venv",
                "-c",
                str(odoo_src),
                "-e",
                str(ent_src),
            ],
        )

        assert result.exit_code == 0
        assert (tmp_project / ".osh" / "odoo").is_symlink()
        assert (tmp_project / ".osh" / "enterprise").is_symlink()
        assert (tmp_project / ".osh" / "config.toml").exists()
        assert "pip install failed" in result.output

    def test_installs_project_requirements(self, tmp_project, monkeypatch):
        """A top-level requirements.txt is installed into the virtualenv."""
        odoo_src = tmp_project / "odoo"
        odoo_src.mkdir(parents=True, exist_ok=True)
        (odoo_src / "odoo-bin").touch()
        (odoo_src / "requirements.txt").touch()
        (tmp_project / "requirements.txt").touch()

        calls = real_commands(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "init",
                "19.0",
                str(tmp_project),
                "--runtime",
                "venv",
                "-c",
                str(odoo_src),
            ],
        )

        assert result.exit_code == 0
        project_req_arg = [str(tmp_project / "requirements.txt")]
        assert any(call[2:5] == ["-r", *project_req_arg] for call in calls)

    def test_reinit_skips_package_installs(self, tmp_project, monkeypatch):
        """A repeated `osh init --runtime=venv` does not reinstall packages.

        The user runs `osh init --runtime=venv` on an already-initialised project —
        with unchanged requirements the slow pip installs are skipped;
        editing requirements.txt makes them run again.
        """
        odoo_src = tmp_project / "odoo"
        odoo_src.mkdir(parents=True)
        (odoo_src / "odoo-bin").touch()
        (odoo_src / "requirements.txt").touch()
        (odoo_src / "setup.py").touch()
        requirements = tmp_project / "requirements.txt"
        requirements.write_text("requests\n")

        calls = real_commands(monkeypatch)
        runner = CliRunner()
        init = [
            "init",
            "19.0",
            str(tmp_project),
            "--runtime",
            "venv",
            "-c",
            str(odoo_src),
        ]

        result = runner.invoke(main, init)
        assert result.exit_code == 0
        assert any("install" in call for call in calls)

        calls.clear()
        result = runner.invoke(main, init)
        assert result.exit_code == 0
        assert "skipping reinstall" in result.output
        assert not any("install" in call for call in calls)

        requirements.write_text("requests\nhttpx\n")
        calls.clear()
        result = runner.invoke(main, init)
        assert result.exit_code == 0
        assert any("install" in call for call in calls)

    def test_smoke_test_succeeds_when_odoo_executable_works(
        self, tmp_project, monkeypatch, fake_odoo_executable
    ):
        """After pip install, a working Odoo executable passes the smoke test."""
        odoo_src = tmp_project / "odoo"
        odoo_src.mkdir(parents=True, exist_ok=True)
        (odoo_src / "odoo-bin").touch()

        real_commands(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "init",
                "19.0",
                str(tmp_project),
                "--runtime",
                "venv",
                "-c",
                str(odoo_src),
            ],
        )

        assert result.exit_code == 0
        assert "Running quick Odoo smoke test" in result.output
        assert "Initialised project directory" in result.output
        assert "Odoo setup incomplete" not in result.output

    def test_smoke_test_failure_still_initializes(
        self, tmp_project, monkeypatch, fake_odoo_executable
    ):
        """A failing smoke test keeps the project initialised and warns."""
        odoo_src = tmp_project / "odoo"
        odoo_src.mkdir(parents=True, exist_ok=True)
        (odoo_src / "odoo-bin").touch()

        fake_odoo_executable.write_text("#!/bin/sh\necho error; exit 1")

        real_commands(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "init",
                "19.0",
                str(tmp_project),
                "--runtime",
                "venv",
                "-c",
                str(odoo_src),
            ],
        )

        assert result.exit_code == 0
        assert "Odoo smoke test failed" in result.output
        assert (tmp_project / ".osh" / "odoo").is_symlink()
        assert (tmp_project / ".osh" / "config.toml").exists()


class TestInitEdition:
    def _make_local_sources(self, tmp_project):
        """Create odoo, enterprise and design-themes source trees in project."""
        odoo = tmp_project / "odoo"
        odoo.mkdir(parents=True, exist_ok=True)
        (odoo / "odoo-bin").touch()

        ent_web = tmp_project / "enterprise" / "web"
        ent_web.mkdir(parents=True, exist_ok=True)
        (ent_web / "__manifest__.py").touch()

        theme = tmp_project / "design-themes" / "theme_buzzy"
        theme.mkdir(parents=True, exist_ok=True)
        (theme / "__manifest__.py").touch()

    def test_ce_skips_enterprise_and_themes(self, tmp_project, monkeypatch):
        """Default --edition ce only links Odoo sources."""
        self._make_local_sources(tmp_project)
        real_commands(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(
            main, ["init", "19.0", str(tmp_project), "--runtime", "venv"]
        )

        assert result.exit_code == 0
        assert (tmp_project / ".osh" / "odoo").is_symlink()
        assert not (tmp_project / ".osh" / "enterprise").exists()
        assert not (tmp_project / ".osh" / "design-themes").exists()

    def test_ee_alias_includes_enterprise(self, tmp_project, monkeypatch):
        """--ee links Odoo and Enterprise but not design-themes."""
        self._make_local_sources(tmp_project)
        real_commands(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(
            main, ["init", "19.0", str(tmp_project), "--runtime", "venv", "--ee"]
        )

        assert result.exit_code == 0
        assert (tmp_project / ".osh" / "odoo").is_symlink()
        assert (tmp_project / ".osh" / "enterprise").is_symlink()
        assert not (tmp_project / ".osh" / "design-themes").exists()

    def test_sh_alias_includes_themes(self, tmp_project, monkeypatch):
        """--sh links Odoo, Enterprise and design-themes."""
        self._make_local_sources(tmp_project)
        real_commands(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(
            main, ["init", "19.0", str(tmp_project), "--runtime", "venv", "--sh"]
        )

        assert result.exit_code == 0
        assert (tmp_project / ".osh" / "odoo").is_symlink()
        assert (tmp_project / ".osh" / "enterprise").is_symlink()
        assert (tmp_project / ".osh" / "design-themes").is_symlink()

    def test_save_writes_user_config(self, tmp_project, monkeypatch, user_config):
        """--save persists the resolved edition to ~/.config/osh/config.toml."""
        self._make_local_sources(tmp_project)
        real_commands(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(
            main, ["init", "19.0", "--sh", "--save", str(tmp_project)]
        )

        assert result.exit_code == 0
        assert user_config.exists()
        assert "edition = 'sh'" in user_config.read_text()

    def test_ce_alias_skips_optional_sources(self, tmp_project, monkeypatch):
        """--ce explicitly selects Community only."""
        self._make_local_sources(tmp_project)
        real_commands(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(
            main, ["init", "19.0", str(tmp_project), "--runtime", "venv", "--ce"]
        )

        assert result.exit_code == 0
        assert (tmp_project / ".osh" / "odoo").is_symlink()
        assert not (tmp_project / ".osh" / "enterprise").exists()
        assert not (tmp_project / ".osh" / "design-themes").exists()

    def test_interactive_confirm_prompt_uses_default(self, tmp_project, monkeypatch):
        """When stdin is a tty, a single confirmation prompt is shown."""
        self._make_local_sources(tmp_project)
        real_commands(monkeypatch)
        monkeypatch.setattr(
            "click.testing._NamedTextIOWrapper.isatty", lambda self: True
        )

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["init", "19.0", str(tmp_project), "--runtime", "venv", "--sh"],
            input="\n",
        )

        assert result.exit_code == 0
        assert "Proceed with initialization?" in result.output
        assert (tmp_project / ".osh" / "odoo").is_symlink()
        assert (tmp_project / ".osh" / "enterprise").is_symlink()
        assert (tmp_project / ".osh" / "design-themes").is_symlink()

    def test_env_var_sets_default_edition(self, tmp_project, monkeypatch):
        """OSH_INIT_EDITION sets the default edition when no CLI flag is given."""
        self._make_local_sources(tmp_project)
        real_commands(monkeypatch)

        runner = CliRunner(env={"OSH_INIT_EDITION": "ee"})
        result = runner.invoke(
            main, ["init", "19.0", str(tmp_project), "--runtime", "venv"]
        )

        assert result.exit_code == 0
        assert (tmp_project / ".osh" / "odoo").is_symlink()
        assert (tmp_project / ".osh" / "enterprise").is_symlink()
        assert not (tmp_project / ".osh" / "design-themes").exists()

    def test_cli_flag_overrides_env_var_edition(self, tmp_project, monkeypatch):
        """An explicit edition alias wins over OSH_INIT_EDITION."""
        self._make_local_sources(tmp_project)
        real_commands(monkeypatch)

        runner = CliRunner(env={"OSH_INIT_EDITION": "sh"})
        result = runner.invoke(
            main, ["init", "19.0", str(tmp_project), "--runtime", "venv", "--ce"]
        )

        assert result.exit_code == 0
        assert (tmp_project / ".osh" / "odoo").is_symlink()
        assert not (tmp_project / ".osh" / "enterprise").exists()
        assert not (tmp_project / ".osh" / "design-themes").exists()

    def test_user_config_sets_default_edition(
        self, tmp_project, monkeypatch, user_config
    ):
        """~/.config/osh/config.toml sets the default edition."""
        self._make_local_sources(tmp_project)
        real_commands(monkeypatch)

        user_config.parent.mkdir(parents=True, exist_ok=True)
        user_config.write_text('[init]\nedition = "sh"\n')

        runner = CliRunner()
        result = runner.invoke(
            main, ["init", "19.0", str(tmp_project), "--runtime", "venv"]
        )

        assert result.exit_code == 0
        assert (tmp_project / ".osh" / "odoo").is_symlink()
        assert (tmp_project / ".osh" / "enterprise").is_symlink()
        assert (tmp_project / ".osh" / "design-themes").is_symlink()

    def test_non_git_dir_warns_and_aborts(self, tmp_path, monkeypatch):
        """Init in a non-git directory warns and aborts without confirmation."""
        target = tmp_path / "nogit"
        target.mkdir()
        runner = CliRunner()
        result = runner.invoke(main, ["init", "19.0", str(target)], input="n\n")
        assert result.exit_code != 0
        assert "not a git repository" in result.output

    def test_non_git_dir_proceeds_with_yes(self, tmp_path, tmp_project, monkeypatch):
        """Init in a non-git directory proceeds with --yes."""
        target = tmp_path / "nogit"
        target.mkdir()
        odoo_src = target / "odoo"
        odoo_src.mkdir()
        (odoo_src / "odoo-bin").touch()
        real_commands(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "init",
                "19.0",
                str(target),
                "--runtime",
                "venv",
                "--yes",
                "-c",
                str(odoo_src),
            ],
        )
        assert result.exit_code == 0
        assert "not a git repository" in result.output
        assert (target / ".osh" / "odoo").is_symlink()


def test_runtime_init_reuses_recorded_version(tmp_project, monkeypatch):
    """``osh init --runtime=<name>`` resolves the version the same way."""
    set_project_config(tmp_project, "init", "version", "18.0")
    odoo_src = tmp_project / "odoo"
    odoo_src.mkdir()
    (odoo_src / "odoo-bin").touch()
    real_commands(monkeypatch)

    result = CliRunner().invoke(
        main, ["init", str(tmp_project), "--runtime", "venv", "--edition", "ce"]
    )

    assert result.exit_code == 0, result.output
    assert "Odoo 18.0" in result.output
    assert get_project_config(tmp_project, "init", "version") == "18.0"
