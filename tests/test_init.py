"""Tests for ``osh init`` source resolution."""

import subprocess
import sys
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from osh.cli import main
from osh.plugins.osh_local.backends import LocalBackend
from osh.sources import (
    DEFAULT_ODOO_URL,
    _cache_has_branch,
    _ensure_cache,
    _find_local_source,
    _install_source_plan,
    _is_git_url,
    _resolve_source,
    _source_branch,
)

from .conftest import real_git_only_subprocess


def _make_bare_repo(tmp_path, name, branches=("master",)):
    """Create a bare git repository with commits on the requested branches."""
    repo = tmp_path / name
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "README").write_text(name)
    subprocess.run(["git", "add", "README"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    for branch in branches[1:]:
        subprocess.run(
            ["git", "checkout", "-b", branch],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / branch).write_text(branch)
        subprocess.run(
            ["git", "add", branch],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", branch],
            cwd=repo,
            check=True,
            capture_output=True,
        )
    subprocess.run(
        ["git", "checkout", branches[0]],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    bare = tmp_path / f"{name}.git"
    subprocess.run(
        ["git", "clone", "--bare", str(repo), str(bare)],
        check=True,
        capture_output=True,
    )
    return bare


def _ensure_source(
    name,
    version,
    source_flag,
    project_source,
    osh_dir,
    default_url,
):
    """Resolve and install a source plan for tests."""
    action, spec, _warning = _resolve_source(
        name, version, source_flag, project_source, osh_dir, default_url
    )

    if action == "cache" and sys.stdin.isatty():
        if not click.confirm(
            f"{name.capitalize()} sources not found in project. "
            f"Use central cache (clone from {default_url} if missing)?",
            default=True,
            err=True,
        ):
            spec = click.prompt(
                "Enter a local path or git URL for "
                f"{name} sources (leave empty to skip)",
                default="",
                show_default=False,
                err=True,
            ).strip()
            if not spec:
                return None
            local_path = Path(spec).expanduser().resolve()
            action = (
                "symlink" if not _is_git_url(spec) and local_path.is_dir() else "clone"
            )
            spec = local_path if action == "symlink" else spec

    return _install_source_plan(name, version, action, spec, osh_dir)


class TestFindLocalSources:
    def test_find_odoo_in_root(self, tmp_project):
        (tmp_project / "odoo-bin").touch()
        assert (
            _find_local_source(tmp_project, ("",), ("odoo-bin",))
            == tmp_project.resolve()
        )

    def test_find_odoo_in_subdirectory(self, tmp_project):
        sub = tmp_project / "odoo"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "odoo-bin").touch()
        assert _find_local_source(tmp_project, ("",), ("odoo-bin",)) == sub.resolve()

    def test_find_enterprise_with_manifest(self, tmp_project):
        ent = tmp_project / "enterprise"
        web = ent / "web"
        web.mkdir(parents=True, exist_ok=True)
        (web / "__manifest__.py").touch()
        assert (
            _find_local_source(
                tmp_project, ("enterprise",), ("*/__manifest__.py", "*/__openerp__.py")
            )
            == ent.resolve()
        )

    def test_find_themes_with_manifest(self, tmp_project):
        themes = tmp_project / "design-themes"
        theme_buzzy = themes / "theme_buzzy"
        theme_buzzy.mkdir(parents=True, exist_ok=True)
        (theme_buzzy / "__manifest__.py").touch()
        assert (
            _find_local_source(
                tmp_project,
                ("design-themes", "themes"),
                ("*/__manifest__.py", "*/__openerp__.py"),
            )
            == themes.resolve()
        )


class TestIsGitUrl:
    @pytest.mark.parametrize(
        ("spec", "expected"),
        [
            ("https://github.com/odoo/odoo.git", True),
            ("git@github.com:odoo/enterprise.git", True),
            ("file:///path/to/repo.git", True),
            ("ssh://git@github.com/odoo/odoo.git", True),
            ("/path/to/odoo", False),
            ("../odoo", False),
            ("C:\\odoo", False),
        ],
    )
    def test_is_git_url(self, spec, expected):
        assert _is_git_url(spec) is expected


class TestEnsureCache:
    def test_creates_mirror(self, tmp_path, patch_cache):
        bare = _make_bare_repo(tmp_path, "odoo")
        cache = _ensure_cache("odoo", "master", f"file://{bare}")

        assert cache == patch_cache / "odoo.git"
        assert cache.exists()
        assert (cache / "config").exists()
        assert _cache_has_branch(cache, "master")

    def test_fetches_missing_version(self, tmp_path, patch_cache):
        bare = _make_bare_repo(tmp_path, "odoo", ("master", "19.0"))
        _ensure_cache("odoo", "master", f"file://{bare}")
        cache = _ensure_cache("odoo", "19.0", f"file://{bare}")

        assert _cache_has_branch(cache, "19.0")


class TestEnsureSource:
    def test_uses_project_source(self, tmp_project, patch_cache):
        src = tmp_project / "odoo"
        src.mkdir(parents=True, exist_ok=True)
        (src / "odoo-bin").touch()
        osh_dir = tmp_project / ".osh"
        osh_dir.mkdir(parents=True, exist_ok=True)

        result = _ensure_source(
            "odoo",
            "19.0",
            None,
            _find_local_source(tmp_project, ("",), ("odoo-bin",)),
            osh_dir,
            DEFAULT_ODOO_URL,
        )

        assert result == osh_dir / "odoo"
        assert (osh_dir / "odoo").is_symlink()
        assert (osh_dir / "odoo").resolve() == src.resolve()

    def test_uses_explicit_local_source(self, tmp_project, patch_cache):
        src = tmp_project / "my-odoo"
        src.mkdir(parents=True, exist_ok=True)
        (src / "odoo-bin").touch()
        osh_dir = tmp_project / ".osh"
        osh_dir.mkdir(parents=True, exist_ok=True)

        result = _ensure_source(
            "odoo",
            "19.0",
            str(src),
            None,
            osh_dir,
            DEFAULT_ODOO_URL,
        )

        assert result == osh_dir / "odoo"
        assert (osh_dir / "odoo").is_symlink()
        assert (osh_dir / "odoo").resolve() == src.resolve()

    def test_clones_explicit_git_url(self, tmp_path, tmp_project, patch_cache):
        bare = _make_bare_repo(tmp_path, "odoo")
        osh_dir = tmp_project / ".osh"
        osh_dir.mkdir(parents=True, exist_ok=True)

        result = _ensure_source(
            "odoo",
            "master",
            f"file://{bare}",
            None,
            osh_dir,
            DEFAULT_ODOO_URL,
        )

        assert result == osh_dir / "odoo"
        assert (osh_dir / "odoo" / ".git").is_dir()
        assert (osh_dir / "odoo" / "README").exists()

    def test_clones_from_cache(self, tmp_path, tmp_project, patch_cache):
        bare = _make_bare_repo(tmp_path, "odoo")
        osh_dir = tmp_project / ".osh"
        osh_dir.mkdir(parents=True, exist_ok=True)

        result = _ensure_source(
            "odoo",
            "master",
            None,
            None,
            osh_dir,
            f"file://{bare}",
        )

        cache = patch_cache / "odoo.git"
        assert cache.exists()
        assert result == osh_dir / "odoo"
        assert (osh_dir / "odoo" / ".git").is_dir()
        assert (osh_dir / "odoo" / "README").exists()

    def test_existing_link_takes_precedence(self, tmp_project, patch_cache):
        existing = tmp_project / "odoo"
        existing.mkdir(parents=True, exist_ok=True)
        osh_dir = tmp_project / ".osh"
        osh_dir.mkdir(parents=True, exist_ok=True)
        (osh_dir / "odoo").symlink_to(existing)

        result = _ensure_source(
            "odoo",
            "19.0",
            "https://example.com/odoo.git",
            None,
            osh_dir,
            DEFAULT_ODOO_URL,
        )

        assert result == osh_dir / "odoo"
        assert (osh_dir / "odoo").resolve() == existing.resolve()

    def test_skip_when_user_declines(self, tmp_project, patch_cache, monkeypatch):
        osh_dir = tmp_project / ".osh"
        osh_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr("tests.test_init.sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("tests.test_init.click.confirm", lambda *a, **kw: False)
        monkeypatch.setattr("tests.test_init.click.prompt", lambda *a, **kw: "")

        result = _ensure_source(
            "odoo",
            "19.0",
            None,
            None,
            osh_dir,
            DEFAULT_ODOO_URL,
        )

        assert result is None
        assert not (osh_dir / "odoo").exists()


class TestInitCommand:
    def test_with_project_sources(self, tmp_project, monkeypatch):
        odoo_src = tmp_project / "odoo"
        odoo_src.mkdir(parents=True, exist_ok=True)
        (odoo_src / "odoo-bin").touch()
        web = tmp_project / "enterprise" / "web"
        web.mkdir(parents=True, exist_ok=True)
        (web / "__manifest__.py").touch()

        real_git_only_subprocess(monkeypatch)
        runner = CliRunner()
        result = runner.invoke(
            main, ["init", "19.0", "--edition", "ee", str(tmp_project)]
        )

        assert result.exit_code == 0
        assert (tmp_project / ".osh" / "odoo").is_symlink()
        assert (tmp_project / ".osh" / "enterprise").is_symlink()
        assert (tmp_project / ".osh" / "config").exists()

    def test_with_source_flags(self, tmp_project, monkeypatch):
        odoo_src = tmp_project / "my-odoo"
        odoo_src.mkdir(parents=True, exist_ok=True)
        (odoo_src / "odoo-bin").touch()
        ent_src = tmp_project / "my-ent"
        web = ent_src / "web"
        web.mkdir(parents=True, exist_ok=True)
        (web / "__manifest__.py").touch()

        real_git_only_subprocess(monkeypatch)
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "init",
                "19.0",
                str(tmp_project),
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

        real_git_only_subprocess(monkeypatch)
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "init",
                "19.0",
                str(tmp_project),
                "-c",
                str(odoo_src),
                "-t",
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
        odoo_bare = _make_bare_repo(tmp_path, "odoo")
        ent_bare = _make_bare_repo(tmp_path, "enterprise")
        themes_bare = _make_bare_repo(tmp_path, "design-themes")
        monkeypatch.setattr("osh.sources.DEFAULT_ODOO_URL", f"file://{odoo_bare}")
        monkeypatch.setattr("osh.sources.DEFAULT_ENTERPRISE_URL", f"file://{ent_bare}")
        monkeypatch.setattr("osh.sources.DEFAULT_THEMES_URL", f"file://{themes_bare}")
        real_git_only_subprocess(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(main, ["init", "master", "--sh", str(tmp_project)])

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

        real_check_call = subprocess.check_call

        def failing_check_call(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args")
            if isinstance(cmd, (list, tuple)) and "git" in cmd:
                return real_check_call(*args, **kwargs)
            raise subprocess.CalledProcessError(1, cmd)

        monkeypatch.setattr(
            "osh.plugins.osh_local.utils.subprocess.check_call", failing_check_call
        )

        runner = CliRunner()
        result = runner.invoke(
            main,
            ["init", "19.0", str(tmp_project), "-c", str(odoo_src), "-e", str(ent_src)],
        )

        assert result.exit_code == 0
        assert (tmp_project / ".osh" / "odoo").is_symlink()
        assert (tmp_project / ".osh" / "enterprise").is_symlink()
        assert (tmp_project / ".osh" / "config").exists()
        assert "pip install failed" in result.output

    def test_installs_project_requirements(self, tmp_project, monkeypatch):
        """A top-level requirements.txt is installed into the virtualenv."""
        odoo_src = tmp_project / "odoo"
        odoo_src.mkdir(parents=True, exist_ok=True)
        (odoo_src / "odoo-bin").touch()
        (odoo_src / "requirements.txt").touch()
        (tmp_project / "requirements.txt").touch()

        calls = real_git_only_subprocess(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(
            main, ["init", "19.0", str(tmp_project), "-c", str(odoo_src)]
        )

        assert result.exit_code == 0
        project_req_arg = [str(tmp_project / "requirements.txt")]
        assert any(call[2:5] == ["-r", *project_req_arg] for call in calls)

    def test_smoke_test_succeeds_when_odoo_executable_works(
        self, tmp_project, monkeypatch, fake_odoo_executable
    ):
        """After pip install, a working Odoo executable passes the smoke test."""
        odoo_src = tmp_project / "odoo"
        odoo_src.mkdir(parents=True, exist_ok=True)
        (odoo_src / "odoo-bin").touch()

        real_git_only_subprocess(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(
            main, ["init", "19.0", str(tmp_project), "-c", str(odoo_src)]
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

        real_git_only_subprocess(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(
            main, ["init", "19.0", str(tmp_project), "-c", str(odoo_src)]
        )

        assert result.exit_code == 0
        assert "Warning: Odoo smoke test failed" in result.output
        assert (tmp_project / ".osh" / "odoo").is_symlink()
        assert (tmp_project / ".osh" / "config").exists()


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
        real_git_only_subprocess(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(main, ["init", "19.0", str(tmp_project)])

        assert result.exit_code == 0
        assert (tmp_project / ".osh" / "odoo").is_symlink()
        assert not (tmp_project / ".osh" / "enterprise").exists()
        assert not (tmp_project / ".osh" / "design-themes").exists()

    def test_ee_alias_includes_enterprise(self, tmp_project, monkeypatch):
        """--ee links Odoo and Enterprise but not design-themes."""
        self._make_local_sources(tmp_project)
        real_git_only_subprocess(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(main, ["init", "19.0", "--ee", str(tmp_project)])

        assert result.exit_code == 0
        assert (tmp_project / ".osh" / "odoo").is_symlink()
        assert (tmp_project / ".osh" / "enterprise").is_symlink()
        assert not (tmp_project / ".osh" / "design-themes").exists()

    def test_sh_alias_includes_themes(self, tmp_project, monkeypatch):
        """--sh links Odoo, Enterprise and design-themes."""
        self._make_local_sources(tmp_project)
        real_git_only_subprocess(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(main, ["init", "19.0", "--sh", str(tmp_project)])

        assert result.exit_code == 0
        assert (tmp_project / ".osh" / "odoo").is_symlink()
        assert (tmp_project / ".osh" / "enterprise").is_symlink()
        assert (tmp_project / ".osh" / "design-themes").is_symlink()

    def test_save_writes_user_config(self, tmp_project, monkeypatch):
        """--save persists the resolved edition to ~/.config/osh/config.toml."""
        self._make_local_sources(tmp_project)
        real_git_only_subprocess(monkeypatch)

        fake_home = tmp_project / "home"
        fake_home.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr("osh.userconfig.Path.home", lambda: fake_home)

        runner = CliRunner()
        result = runner.invoke(
            main, ["init", "19.0", "--sh", "--save", str(tmp_project)]
        )

        assert result.exit_code == 0
        config_file = fake_home / ".config" / "osh" / "config.toml"
        assert config_file.exists()
        assert 'edition = "sh"' in config_file.read_text()

    def test_ce_alias_skips_optional_sources(self, tmp_project, monkeypatch):
        """--ce explicitly selects Community only."""
        self._make_local_sources(tmp_project)
        real_git_only_subprocess(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(main, ["init", "19.0", "--ce", str(tmp_project)])

        assert result.exit_code == 0
        assert (tmp_project / ".osh" / "odoo").is_symlink()
        assert not (tmp_project / ".osh" / "enterprise").exists()
        assert not (tmp_project / ".osh" / "design-themes").exists()

    def test_interactive_confirm_prompt_uses_default(self, tmp_project, monkeypatch):
        """When stdin is a tty, a single confirmation prompt is shown."""
        self._make_local_sources(tmp_project)
        real_git_only_subprocess(monkeypatch)
        monkeypatch.setattr(
            "click.testing._NamedTextIOWrapper.isatty", lambda self: True
        )

        runner = CliRunner()
        result = runner.invoke(
            main, ["init", "19.0", "--sh", str(tmp_project)], input="\n"
        )

        assert result.exit_code == 0
        assert "Proceed with initialization?" in result.output
        assert (tmp_project / ".osh" / "odoo").is_symlink()
        assert (tmp_project / ".osh" / "enterprise").is_symlink()
        assert (tmp_project / ".osh" / "design-themes").is_symlink()

    def test_env_var_sets_default_edition(self, tmp_project, monkeypatch):
        """OSH_INIT_EDITION sets the default edition when no CLI flag is given."""
        self._make_local_sources(tmp_project)
        real_git_only_subprocess(monkeypatch)

        runner = CliRunner(env={"OSH_INIT_EDITION": "ee"})
        result = runner.invoke(main, ["init", "19.0", str(tmp_project)])

        assert result.exit_code == 0
        assert (tmp_project / ".osh" / "odoo").is_symlink()
        assert (tmp_project / ".osh" / "enterprise").is_symlink()
        assert not (tmp_project / ".osh" / "design-themes").exists()

    def test_user_config_sets_default_edition(self, tmp_project, monkeypatch):
        """~/.config/osh/config.toml sets the default edition."""
        self._make_local_sources(tmp_project)
        real_git_only_subprocess(monkeypatch)

        fake_home = tmp_project / "home"
        fake_home.mkdir(parents=True, exist_ok=True)
        config_dir = fake_home / ".config" / "osh"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "config.toml").write_text('[init]\nedition = "sh"\n')
        monkeypatch.setattr("osh.userconfig.Path.home", lambda: fake_home)

        runner = CliRunner()
        result = runner.invoke(main, ["init", "19.0", str(tmp_project)])

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
        real_git_only_subprocess(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(
            main, ["init", "19.0", str(target), "--yes", "-c", str(odoo_src)]
        )
        assert result.exit_code == 0
        assert "not a git repository" in result.output
        assert (target / ".osh" / "odoo").is_symlink()


class TestDoctorVersionReporting:
    def test_local_diagnose_reports_installed_odoo_version(
        self, tmp_project, fake_odoo_executable
    ):
        """``osh doctor`` (via LocalBackend.diagnose) reports the installed Odoo version."""
        backend = LocalBackend()
        diagnostics = backend.diagnose(tmp_project)
        assert diagnostics.info["odoo_version"] == "odoo 19.0"


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


class TestSourceVersionSwitching:
    def test_managed_source_is_replaced_for_a_different_version(
        self, tmp_path, tmp_project, patch_cache, monkeypatch
    ):
        """Re-running ``osh init`` with a different version replaces managed sources."""
        bare = _make_bare_repo(tmp_path, "odoo", ("master", "19.0"))
        monkeypatch.setattr("osh.sources.DEFAULT_ODOO_URL", f"file://{bare}")

        osh_dir = tmp_project / ".osh"
        osh_dir.mkdir(parents=True, exist_ok=True)

        _ensure_source(
            "odoo",
            "master",
            None,
            None,
            osh_dir,
            f"file://{bare}",
        )
        assert _source_branch(osh_dir / "odoo") == "master"

        _ensure_source(
            "odoo",
            "19.0",
            None,
            None,
            osh_dir,
            f"file://{bare}",
        )
        assert _source_branch(osh_dir / "odoo") == "19.0"
