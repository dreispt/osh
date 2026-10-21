"""Tests for the built-in venv runtime plugin."""

import shutil

import pytest
from click.testing import CliRunner

from osh.cli import main
from osh.db import get_project_config, set_project_config
from osh.plugins.osh_runtime_venv.runtimes import VenvRuntime
from tests.helpers import make_bare_repo

from .conftest import make_odoo_source, real_commands

pytestmark = pytest.mark.skipif(
    shutil.which("uv") is None,
    reason="the venv runtime tests require 'uv' on PATH",
)


def test_diagnose_quiet_until_requirements_change_after_init(tmp_project):
    """Editing requirements.txt after init prompts an ``osh init`` hint.

    Init fingerprints the requirement files it installed from; the check
    is silent until one of them changes.
    """
    odoo_src = make_odoo_source(tmp_project / "odoo")
    requirements = tmp_project / "requirements.txt"
    requirements.write_text("pip\n")
    result = CliRunner().invoke(
        main,
        ["init", "19.0", str(tmp_project), "--runtime", "venv", "-c", str(odoo_src)],
    )
    assert result.exit_code == 0

    runtime = VenvRuntime()
    warnings = runtime.diagnose(tmp_project, phase="run").warnings
    assert not any("osh init venv" in w for w in warnings)

    requirements.write_text("pip\nwheel\n")
    warnings = runtime.diagnose(tmp_project, phase="run").warnings
    assert any("osh init venv" in w for w in warnings)


def test_init_fingerprint_accepts_inputs_without_reinstall(tmp_project, monkeypatch):
    """``osh init --fingerprint`` re-fingerprints inputs without reinstalling.

    A requirements edit after init flags the environment as stale; a bare
    ``--fingerprint`` targets the project's active runtime and accepts the
    inputs as built — no pip installs run and the warning clears.
    """
    odoo_src = make_odoo_source(tmp_project / "odoo")
    requirements = tmp_project / "requirements.txt"
    requirements.write_text("pip\n")
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

    requirements.write_text("pip\nwheel\n")
    warnings = VenvRuntime().diagnose(tmp_project, phase="run").warnings
    assert any("osh init venv" in w for w in warnings)

    calls.clear()
    result = CliRunner().invoke(main, ["init", str(tmp_project), "--fingerprint"])
    assert result.exit_code == 0
    assert not any("install" in call for call in calls)

    warnings = VenvRuntime().diagnose(tmp_project, phase="run").warnings
    assert not any("osh init venv" in w for w in warnings)


class TestInitCommand:
    def test_with_project_sources(self, tmp_project):
        make_odoo_source(tmp_project / "odoo")
        web = tmp_project / "enterprise" / "web"
        web.mkdir(parents=True, exist_ok=True)
        (web / "__manifest__.py").touch()

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["init", "19.0", str(tmp_project), "--runtime", "venv", "--edition", "ee"],
        )

        assert result.exit_code == 0
        assert (tmp_project / ".osh" / "odoo").is_symlink()
        assert (tmp_project / ".osh" / "enterprise").is_symlink()
        assert (tmp_project / ".osh" / "config.toml").exists()

    def test_with_source_flags(self, tmp_project):
        odoo_src = make_odoo_source(tmp_project / "my-odoo")
        ent_src = tmp_project / "my-ent"
        web = ent_src / "web"
        web.mkdir(parents=True, exist_ok=True)
        (web / "__manifest__.py").touch()

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

    def test_with_themes_source_flag(self, tmp_project):
        odoo_src = make_odoo_source(tmp_project / "my-odoo")
        themes_src = tmp_project / "my-themes"
        theme = themes_src / "theme_buzzy"
        theme.mkdir(parents=True, exist_ok=True)
        (theme / "__manifest__.py").touch()

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

    def test_pip_install_failure_still_initializes(self, tmp_project):
        """If pip install fails, the project environment is still created.

        A requirements.txt naming a package that cannot be resolved makes
        the real pip install fail.
        """
        odoo_src = make_odoo_source(tmp_project / "odoo")
        (odoo_src / "requirements.txt").write_text("osh-nonexistent-package\n")
        ent_src = tmp_project / "enterprise"
        web = ent_src / "web"
        web.mkdir(parents=True, exist_ok=True)
        (web / "__manifest__.py").touch()

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
                "--installer",
                "pip",
            ],
        )

        assert result.exit_code == 0
        assert (tmp_project / ".osh" / "odoo").is_symlink()
        assert (tmp_project / ".osh" / "enterprise").is_symlink()
        assert (tmp_project / ".osh" / "config.toml").exists()
        assert "pip install failed" in result.output

    def test_installs_project_requirements(self, tmp_project, monkeypatch):
        """A top-level requirements.txt is installed into the virtualenv."""
        odoo_src = make_odoo_source(tmp_project / "odoo")
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
                "--installer",
                "pip",
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
        odoo_src = make_odoo_source(tmp_project / "odoo")
        (odoo_src / "requirements.txt").touch()
        requirements = tmp_project / "requirements.txt"
        requirements.write_text("pip\n")

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

        requirements.write_text("pip\nwheel\n")
        calls.clear()
        result = runner.invoke(main, init)
        assert result.exit_code == 0
        assert any("install" in call for call in calls)

    def test_smoke_test_succeeds_when_odoo_executable_works(self, tmp_project):
        """After the editable install, ``odoo-bin --version`` passes the smoke test."""
        odoo_src = make_odoo_source(tmp_project / "odoo")

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

    def test_smoke_test_failure_still_initializes(self, tmp_project):
        """A failing smoke test keeps the project initialised and warns."""
        odoo_src = make_odoo_source(tmp_project / "odoo", exit_code=1)

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


class TestInstaller:
    """Which package installer ``osh init venv`` drives: uv or pip."""

    def _odoo_source(self, tmp_project):
        return make_odoo_source(tmp_project / "odoo")

    def _init_args(self, tmp_project, odoo_src):
        return [
            "init",
            "19.0",
            str(tmp_project),
            "--runtime",
            "venv",
            "-c",
            str(odoo_src),
        ]

    def test_uv_used_when_on_path(self, tmp_project, monkeypatch):
        """With uv on PATH, 'osh init venv' builds the environment with uv.

        'uv venv' creates the real .venv and 'uv pip' installs the seed
        packages, the Odoo requirements and the editable Odoo source —
        'python -m venv' and the venv's pip never run.
        """
        odoo_src = self._odoo_source(tmp_project)
        (odoo_src / "requirements.txt").touch()

        calls = real_commands(monkeypatch)
        result = CliRunner().invoke(main, self._init_args(tmp_project, odoo_src))

        assert result.exit_code == 0
        venv_python = tmp_project / ".venv" / "bin" / "python"
        odoo_link = tmp_project / ".osh" / "odoo"
        uv_cmds = [" ".join(map(str, c[1:])) for c in calls if c[0] == "uv"]
        assert any(c.startswith("venv --python") for c in uv_cmds)
        assert any(f"pip install --python {venv_python} pip" in c for c in uv_cmds)
        assert any(c.endswith(f"-r {odoo_link}/requirements.txt") for c in uv_cmds)
        assert any(c.endswith(f"-e {odoo_link}") for c in uv_cmds)
        assert not any({"-m", "venv"} <= set(call) for call in calls)
        assert not any(str(call[0]).endswith("pip") for call in calls)
        # The editable install is real: its odoo-bin lands in the venv.
        assert (venv_python.parent / "odoo-bin").is_file()

    def test_installer_pip_forces_pip(self, tmp_project, monkeypatch):
        """--installer pip keeps the pip path even when uv is on PATH."""
        odoo_src = self._odoo_source(tmp_project)

        calls = real_commands(monkeypatch)
        result = CliRunner().invoke(
            main, [*self._init_args(tmp_project, odoo_src), "--installer", "pip"]
        )

        assert result.exit_code == 0
        assert not any(call[0] == "uv" for call in calls)
        assert any(str(call[0]).endswith("pip") for call in calls)

    def test_installer_uv_without_uv_fails(self, tmp_project, monkeypatch):
        """--installer uv aborts when uv is not on PATH."""
        odoo_src = self._odoo_source(tmp_project)

        monkeypatch.setenv("PATH", "")
        result = CliRunner().invoke(
            main, [*self._init_args(tmp_project, odoo_src), "--installer", "uv"]
        )

        assert result.exit_code != 0
        assert "'uv' was requested" in result.output
        assert not (tmp_project / ".venv").exists()

    def test_installer_choice_remembered_for_reinit(self, tmp_project, monkeypatch):
        """An explicit --installer uv is reused by later re-inits.

        The choice lands in .osh/config.toml; editing requirements.txt and
        re-running 'osh init venv' without the flag keeps installing with
        uv — no second venv is created either.
        """
        odoo_src = self._odoo_source(tmp_project)
        requirements = tmp_project / "requirements.txt"
        requirements.write_text("pip\n")

        calls = real_commands(monkeypatch)
        runner = CliRunner()
        init = self._init_args(tmp_project, odoo_src)
        result = runner.invoke(main, [*init, "--installer", "uv"])
        assert result.exit_code == 0
        assert get_project_config(tmp_project, "init", "installer") == "uv"

        requirements.write_text("pip\nwheel\n")
        calls.clear()
        result = runner.invoke(main, init)

        assert result.exit_code == 0
        assert any(call[:3] == ["uv", "pip", "install"] for call in calls)
        assert not any(call[:2] == ["uv", "venv"] for call in calls)
        assert not any(str(call[0]).endswith("pip") for call in calls)

    def test_stored_uv_falls_back_to_pip(self, tmp_project, monkeypatch):
        """A recorded 'uv' choice falls back to pip when uv leaves PATH.

        The project was initialised with uv on another machine; on this one
        re-init warns and reinstalls with the venv's own pip.
        """
        odoo_src = self._odoo_source(tmp_project)
        requirements = tmp_project / "requirements.txt"
        requirements.write_text("pip\n")

        calls = real_commands(monkeypatch)
        runner = CliRunner()
        init = self._init_args(tmp_project, odoo_src)
        result = runner.invoke(main, [*init, "--installer", "uv"])
        assert result.exit_code == 0

        requirements.write_text("pip\nwheel\n")
        calls.clear()
        monkeypatch.setenv("PATH", "")
        result = runner.invoke(main, init)

        assert result.exit_code == 0
        assert "falling back to pip" in result.output
        assert any(str(call[0]).endswith("pip") for call in calls)


class TestInitEdition:
    def _make_local_sources(self, tmp_project):
        """Create odoo, enterprise and design-themes source trees in project."""
        make_odoo_source(tmp_project / "odoo")

        ent_web = tmp_project / "enterprise" / "web"
        ent_web.mkdir(parents=True, exist_ok=True)
        (ent_web / "__manifest__.py").touch()

        theme = tmp_project / "design-themes" / "theme_buzzy"
        theme.mkdir(parents=True, exist_ok=True)
        (theme / "__manifest__.py").touch()

    def test_ce_skips_enterprise_and_themes(self, tmp_project):
        """Default --edition ce only links Odoo sources."""
        self._make_local_sources(tmp_project)

        runner = CliRunner()
        result = runner.invoke(
            main, ["init", "19.0", str(tmp_project), "--runtime", "venv"]
        )

        assert result.exit_code == 0
        assert (tmp_project / ".osh" / "odoo").is_symlink()
        assert not (tmp_project / ".osh" / "enterprise").exists()
        assert not (tmp_project / ".osh" / "design-themes").exists()

    def test_ee_alias_includes_enterprise(self, tmp_project):
        """--ee links Odoo and Enterprise but not design-themes."""
        self._make_local_sources(tmp_project)

        runner = CliRunner()
        result = runner.invoke(
            main, ["init", "19.0", str(tmp_project), "--runtime", "venv", "--ee"]
        )

        assert result.exit_code == 0
        assert (tmp_project / ".osh" / "odoo").is_symlink()
        assert (tmp_project / ".osh" / "enterprise").is_symlink()
        assert not (tmp_project / ".osh" / "design-themes").exists()

    def test_sh_alias_includes_themes(self, tmp_project):
        """--sh links Odoo, Enterprise and design-themes."""
        self._make_local_sources(tmp_project)

        runner = CliRunner()
        result = runner.invoke(
            main, ["init", "19.0", str(tmp_project), "--runtime", "venv", "--sh"]
        )

        assert result.exit_code == 0
        assert (tmp_project / ".osh" / "odoo").is_symlink()
        assert (tmp_project / ".osh" / "enterprise").is_symlink()
        assert (tmp_project / ".osh" / "design-themes").is_symlink()

    def test_save_writes_user_config(self, tmp_project, user_config):
        """--save persists the resolved edition to ~/.config/osh/config.toml."""
        self._make_local_sources(tmp_project)

        runner = CliRunner()
        result = runner.invoke(
            main, ["init", "19.0", "--sh", "--save", str(tmp_project)]
        )

        assert result.exit_code == 0
        assert user_config.exists()
        assert "edition = 'sh'" in user_config.read_text()

    def test_ce_alias_skips_optional_sources(self, tmp_project):
        """--ce explicitly selects Community only."""
        self._make_local_sources(tmp_project)

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

    def test_env_var_sets_default_edition(self, tmp_project):
        """OSH_INIT_EDITION sets the default edition when no CLI flag is given."""
        self._make_local_sources(tmp_project)

        runner = CliRunner(env={"OSH_INIT_EDITION": "ee"})
        result = runner.invoke(
            main, ["init", "19.0", str(tmp_project), "--runtime", "venv"]
        )

        assert result.exit_code == 0
        assert (tmp_project / ".osh" / "odoo").is_symlink()
        assert (tmp_project / ".osh" / "enterprise").is_symlink()
        assert not (tmp_project / ".osh" / "design-themes").exists()

    def test_cli_flag_overrides_env_var_edition(self, tmp_project):
        """An explicit edition alias wins over OSH_INIT_EDITION."""
        self._make_local_sources(tmp_project)

        runner = CliRunner(env={"OSH_INIT_EDITION": "sh"})
        result = runner.invoke(
            main, ["init", "19.0", str(tmp_project), "--runtime", "venv", "--ce"]
        )

        assert result.exit_code == 0
        assert (tmp_project / ".osh" / "odoo").is_symlink()
        assert not (tmp_project / ".osh" / "enterprise").exists()
        assert not (tmp_project / ".osh" / "design-themes").exists()

    def test_user_config_sets_default_edition(self, tmp_project, user_config):
        """~/.config/osh/config.toml sets the default edition."""
        self._make_local_sources(tmp_project)

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

    def test_non_git_dir_warns_and_aborts(self, tmp_path):
        """Init in a non-git directory warns and aborts without confirmation."""
        target = tmp_path / "nogit"
        target.mkdir()
        runner = CliRunner()
        result = runner.invoke(main, ["init", "19.0", str(target)], input="n\n")
        assert result.exit_code != 0
        assert "not a git repository" in result.output

    def test_non_git_dir_proceeds_with_yes(self, tmp_path, tmp_project):
        """Init in a non-git directory proceeds with --yes."""
        target = tmp_path / "nogit"
        target.mkdir()
        odoo_src = make_odoo_source(target / "odoo")

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


def test_runtime_init_reuses_recorded_version(tmp_project):
    """``osh init --runtime=<name>`` resolves the version the same way."""
    set_project_config(tmp_project, "init", "version", "18.0")
    make_odoo_source(tmp_project / "odoo")

    result = CliRunner().invoke(
        main, ["init", str(tmp_project), "--runtime", "venv", "--edition", "ce"]
    )

    assert result.exit_code == 0, result.output
    assert "Odoo 18.0" in result.output
    assert get_project_config(tmp_project, "init", "version") == "18.0"
