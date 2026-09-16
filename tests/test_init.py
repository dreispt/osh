"""Tests for ``osh init`` source resolution."""

import os
import subprocess
import sys
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from osh.cli import main
from osh.commands import init_cmd
from osh.common import find_enclosing_project, find_nested_projects
from osh.config import get_init_parent
from osh.db import get_project_config, set_project_config
from osh.plugins.osh_backend_venv.backends import VenvBackend
from osh.sources import (
    DEFAULT_ODOO_URL,
    _cache_has_branch,
    _ensure_repo_cache,
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
        assert _find_local_source(tmp_project, ("",), ("odoo-bin",)) == (
            tmp_project.resolve(),
            False,
        )

    def test_find_odoo_in_subdirectory(self, tmp_project):
        sub = tmp_project / "odoo"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "odoo-bin").touch()
        assert _find_local_source(tmp_project, ("",), ("odoo-bin",)) == (
            sub.resolve(),
            False,
        )

    def test_find_enterprise_with_manifest(self, tmp_project):
        ent = tmp_project / "enterprise"
        web = ent / "web"
        web.mkdir(parents=True, exist_ok=True)
        (web / "__manifest__.py").touch()
        assert _find_local_source(
            tmp_project, ("enterprise",), ("*/__manifest__.py", "*/__openerp__.py")
        ) == (ent.resolve(), False)

    def test_find_themes_with_manifest(self, tmp_project):
        themes = tmp_project / "design-themes"
        theme_buzzy = themes / "theme_buzzy"
        theme_buzzy.mkdir(parents=True, exist_ok=True)
        (theme_buzzy / "__manifest__.py").touch()
        assert _find_local_source(
            tmp_project,
            ("design-themes", "themes"),
            ("*/__manifest__.py", "*/__openerp__.py"),
        ) == (themes.resolve(), False)

    def test_find_enterprise_with_pattern(self, tmp_project):
        ent_copy = tmp_project / "my-enterprise-dir"
        web = ent_copy / "web"
        web.mkdir(parents=True, exist_ok=True)
        (web / "__manifest__.py").touch()
        path, requires_confirmation = _find_local_source(
            tmp_project,
            ("enterprise", "enterprise-copy", "*enterprise*"),
            ("*/__manifest__.py", "*/__openerp__.py"),
        )
        assert path == ent_copy.resolve()
        # Should require confirmation since it was found via glob pattern
        assert requires_confirmation


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
        cache = _ensure_repo_cache("odoo", "master", f"file://{bare}")

        assert cache == patch_cache / "odoo.git"
        assert cache.exists()
        assert (cache / "config").exists()
        assert _cache_has_branch(cache, "master")

    def test_fetches_missing_version(self, tmp_path, patch_cache):
        bare = _make_bare_repo(tmp_path, "odoo", ("master", "19.0"))
        _ensure_repo_cache("odoo", "master", f"file://{bare}")
        cache = _ensure_repo_cache("odoo", "19.0", f"file://{bare}")

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
        # In-project sources link relatively so the link survives host
        # moves and container mounts (e.g. /mnt/extra-addons).
        assert not Path(os.readlink(osh_dir / "odoo")).is_absolute()

    def test_links_external_source_absolutely(self, tmp_path, tmp_project, patch_cache):
        """Sources outside the project keep an absolute symlink."""
        src = tmp_path / "external-odoo"
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
        assert Path(os.readlink(osh_dir / "odoo")).is_absolute()
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
            main, ["venv", "init", "19.0", "--edition", "ee", str(tmp_project)]
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

        real_git_only_subprocess(monkeypatch)
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "venv",
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
                "venv",
                "init",
                "19.0",
                str(tmp_project),
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
        odoo_bare = _make_bare_repo(tmp_path, "odoo")
        ent_bare = _make_bare_repo(tmp_path, "enterprise")
        themes_bare = _make_bare_repo(tmp_path, "design-themes")
        monkeypatch.setattr("osh.sources.DEFAULT_ODOO_URL", f"file://{odoo_bare}")
        monkeypatch.setattr("osh.sources.DEFAULT_ENTERPRISE_URL", f"file://{ent_bare}")
        monkeypatch.setattr("osh.sources.DEFAULT_THEMES_URL", f"file://{themes_bare}")
        real_git_only_subprocess(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(
            main, ["venv", "init", "master", "--sh", str(tmp_project)]
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
            "osh.plugins.osh_backend_venv.utils.run_subprocess",
            lambda *args, **kwargs: (1, "", ""),
        )

        # Force the use of the running interpreter so venv.create is used.
        current_version = f"{sys.version_info.major}.{sys.version_info.minor}"
        monkeypatch.setattr(
            "osh.plugins.osh_backend_venv.utils.resolve_python_for_odoo",
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
                "venv",
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

        calls = real_git_only_subprocess(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(
            main, ["venv", "init", "19.0", str(tmp_project), "-c", str(odoo_src)]
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
            main, ["venv", "init", "19.0", str(tmp_project), "-c", str(odoo_src)]
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
            main, ["venv", "init", "19.0", str(tmp_project), "-c", str(odoo_src)]
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
        real_git_only_subprocess(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(main, ["venv", "init", "19.0", str(tmp_project)])

        assert result.exit_code == 0
        assert (tmp_project / ".osh" / "odoo").is_symlink()
        assert not (tmp_project / ".osh" / "enterprise").exists()
        assert not (tmp_project / ".osh" / "design-themes").exists()

    def test_ee_alias_includes_enterprise(self, tmp_project, monkeypatch):
        """--ee links Odoo and Enterprise but not design-themes."""
        self._make_local_sources(tmp_project)
        real_git_only_subprocess(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(main, ["venv", "init", "19.0", "--ee", str(tmp_project)])

        assert result.exit_code == 0
        assert (tmp_project / ".osh" / "odoo").is_symlink()
        assert (tmp_project / ".osh" / "enterprise").is_symlink()
        assert not (tmp_project / ".osh" / "design-themes").exists()

    def test_sh_alias_includes_themes(self, tmp_project, monkeypatch):
        """--sh links Odoo, Enterprise and design-themes."""
        self._make_local_sources(tmp_project)
        real_git_only_subprocess(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(main, ["venv", "init", "19.0", "--sh", str(tmp_project)])

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
        monkeypatch.setattr("osh.config.Path.home", lambda: fake_home)

        runner = CliRunner()
        result = runner.invoke(
            main, ["init", "19.0", "--sh", "--save", str(tmp_project)]
        )

        assert result.exit_code == 0
        config_file = fake_home / ".config" / "osh" / "config.toml"
        assert config_file.exists()
        assert "edition = 'sh'" in config_file.read_text()

    def test_ce_alias_skips_optional_sources(self, tmp_project, monkeypatch):
        """--ce explicitly selects Community only."""
        self._make_local_sources(tmp_project)
        real_git_only_subprocess(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(main, ["venv", "init", "19.0", "--ce", str(tmp_project)])

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
            main, ["venv", "init", "19.0", "--sh", str(tmp_project)], input="\n"
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
        result = runner.invoke(main, ["venv", "init", "19.0", str(tmp_project)])

        assert result.exit_code == 0
        assert (tmp_project / ".osh" / "odoo").is_symlink()
        assert (tmp_project / ".osh" / "enterprise").is_symlink()
        assert not (tmp_project / ".osh" / "design-themes").exists()

    def test_cli_flag_overrides_env_var_edition(self, tmp_project, monkeypatch):
        """An explicit edition alias wins over OSH_INIT_EDITION."""
        self._make_local_sources(tmp_project)
        real_git_only_subprocess(monkeypatch)

        runner = CliRunner(env={"OSH_INIT_EDITION": "sh"})
        result = runner.invoke(main, ["venv", "init", "19.0", "--ce", str(tmp_project)])

        assert result.exit_code == 0
        assert (tmp_project / ".osh" / "odoo").is_symlink()
        assert not (tmp_project / ".osh" / "enterprise").exists()
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
        monkeypatch.setattr("osh.config.Path.home", lambda: fake_home)

        runner = CliRunner()
        result = runner.invoke(main, ["venv", "init", "19.0", str(tmp_project)])

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
            main, ["venv", "init", "19.0", str(target), "--yes", "-c", str(odoo_src)]
        )
        assert result.exit_code == 0
        assert "not a git repository" in result.output
        assert (target / ".osh" / "odoo").is_symlink()


class TestInitVersion:
    """``osh init`` without a VERSION argument reuses recorded defaults."""

    def test_reinit_without_version_uses_recorded(self, tmp_project):
        """The recorded ``init.version`` is reused when VERSION is omitted."""
        set_project_config(tmp_project, "init", "version", "18.0")

        result = CliRunner().invoke(main, ["init", "--edition", "ce", str(tmp_project)])

        assert result.exit_code == 0, result.output
        assert "Odoo 18.0" in result.output
        assert get_project_config(tmp_project, "init", "version") == "18.0"

    def test_explicit_version_overrides_recorded(self, tmp_project):
        """An explicit VERSION argument wins over the recorded one."""
        set_project_config(tmp_project, "init", "version", "18.0")

        result = CliRunner().invoke(
            main, ["init", "19.0", "--edition", "ce", str(tmp_project)]
        )

        assert result.exit_code == 0, result.output
        assert get_project_config(tmp_project, "init", "version") == "19.0"

    def test_env_var_sets_default_version(self, tmp_project):
        """OSH_INIT_VERSION supplies the version when VERSION is omitted."""
        runner = CliRunner(env={"OSH_INIT_VERSION": "19.0"})
        result = runner.invoke(main, ["init", "--edition", "ce", str(tmp_project)])

        assert result.exit_code == 0, result.output
        assert get_project_config(tmp_project, "init", "version") == "19.0"

    def test_user_config_sets_default_version(self, tmp_project, monkeypatch):
        """``[init] version`` in the user config supplies a default."""
        fake_home = tmp_project / "home"
        config_dir = fake_home / ".config" / "osh"
        config_dir.mkdir(parents=True)
        (config_dir / "config.toml").write_text('[init]\nversion = "19.0"\n')
        monkeypatch.setattr("osh.config.Path.home", lambda: fake_home)

        result = CliRunner().invoke(main, ["init", "--edition", "ce", str(tmp_project)])

        assert result.exit_code == 0, result.output
        assert get_project_config(tmp_project, "init", "version") == "19.0"

    def test_recorded_version_beats_user_default(self, tmp_project, monkeypatch):
        """A recorded project version is never overridden by a user default."""
        fake_home = tmp_project / "home"
        config_dir = fake_home / ".config" / "osh"
        config_dir.mkdir(parents=True)
        (config_dir / "config.toml").write_text('[init]\nversion = "19.0"\n')
        monkeypatch.setattr("osh.config.Path.home", lambda: fake_home)
        set_project_config(tmp_project, "init", "version", "18.0")

        result = CliRunner().invoke(main, ["init", "--edition", "ce", str(tmp_project)])

        assert result.exit_code == 0, result.output
        assert get_project_config(tmp_project, "init", "version") == "18.0"

    def test_interactive_version_prompt(self, tmp_project, monkeypatch):
        """Interactive init without a resolvable version prompts for one."""
        monkeypatch.setattr(
            "click.testing._NamedTextIOWrapper.isatty", lambda self: True
        )

        result = CliRunner().invoke(
            main, ["init", "--edition", "ce", str(tmp_project)], input="17.0\n"
        )

        assert result.exit_code == 0, result.output
        assert "Odoo version" in result.output
        assert get_project_config(tmp_project, "init", "version") == "17.0"

    def test_missing_version_errors_non_interactive(self, tmp_path):
        """A fresh project without a resolvable version fails with guidance."""
        target = tmp_path / "fresh"
        (target / ".git").mkdir(parents=True)

        result = CliRunner().invoke(main, ["init", "--edition", "ce", str(target)])

        assert result.exit_code != 0
        assert "Missing VERSION" in result.output
        assert not (target / ".osh").exists()

    def test_backend_init_reuses_recorded_version(self, tmp_project, monkeypatch):
        """``osh <backend> init`` resolves the version the same way."""
        set_project_config(tmp_project, "init", "version", "18.0")
        odoo_src = tmp_project / "odoo"
        odoo_src.mkdir()
        (odoo_src / "odoo-bin").touch()
        real_git_only_subprocess(monkeypatch)

        result = CliRunner().invoke(
            main, ["venv", "init", "--edition", "ce", str(tmp_project)]
        )

        assert result.exit_code == 0, result.output
        assert "Odoo 18.0" in result.output
        assert get_project_config(tmp_project, "init", "version") == "18.0"


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


def _raise_backend_init_error(*args, **kwargs):
    raise click.ClickException("backend init failed")


class TestInitRollback:
    """A failed or aborted init must not leave a stale ``.osh`` marker."""

    def test_failed_backend_init_removes_new_osh_dir(self, tmp_path, monkeypatch):
        """``osh venv init`` failure removes the ``.osh`` dir it created."""
        target = tmp_path / "fresh"
        target.mkdir()
        (target / ".git").mkdir()
        monkeypatch.setattr(VenvBackend, "init", _raise_backend_init_error)

        result = CliRunner().invoke(
            main, ["venv", "init", "19.0", "--edition", "ce", str(target)]
        )

        assert result.exit_code != 0
        assert not (target / ".osh").exists()

    def test_failed_backend_init_keeps_existing_osh_dir(self, tmp_project, monkeypatch):
        """A failed re-init never removes a ``.osh`` holding existing state."""
        marker = tmp_project / ".osh" / "keep.txt"
        marker.write_text("keep")
        monkeypatch.setattr(VenvBackend, "init", _raise_backend_init_error)

        result = CliRunner().invoke(
            main, ["venv", "init", "19.0", "--edition", "ce", str(tmp_project)]
        )

        assert result.exit_code != 0
        assert marker.exists()

    def test_failed_backend_init_removes_empty_osh_dir(self, tmp_project, monkeypatch):
        """An empty ``.osh`` holds no state and is removed on failure."""
        monkeypatch.setattr(VenvBackend, "init", _raise_backend_init_error)

        result = CliRunner().invoke(
            main, ["venv", "init", "19.0", "--edition", "ce", str(tmp_project)]
        )

        assert result.exit_code != 0
        assert not (tmp_project / ".osh").exists()

    def test_declined_confirmation_removes_new_osh_dir(self, tmp_path, monkeypatch):
        """Declining the init plan removes the ``.osh`` the run created."""
        target = tmp_path / "fresh"
        target.mkdir()
        (target / ".git").mkdir()
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(click, "confirm", lambda *a, **kw: False)

        ctx = click.Context(init_cmd.init)
        with pytest.raises(click.ClickException, match="Aborted"):
            with init_cmd._rollback_new_osh_dir(target):
                init_cmd.base_init(
                    ctx,
                    target,
                    version="19.0",
                    edition="ce",
                    save=False,
                    assume_yes=False,
                    dry_run=False,
                    dev=True,
                )
                init_cmd.run_backend_init(
                    ctx,
                    VenvBackend(),
                    target,
                    version="19.0",
                    edition="ce",
                    assume_yes=False,
                    dry_run=False,
                )

        assert not (target / ".osh").exists()

    def test_user_abort_prints_no_rollback_note(self, tmp_path, capsys):
        """Declining a prompt is not a rollback; no 'left untouched' note."""
        target = tmp_path / "proj"
        (target / ".osh").mkdir(parents=True)
        (target / ".osh" / "keep.txt").touch()

        with pytest.raises(click.ClickException, match="Aborted"):
            with init_cmd._rollback_new_osh_dir(target):
                raise click.ClickException("Aborted.")

        assert "left untouched" not in capsys.readouterr().err

    def test_failure_keeps_existing_osh_note(self, tmp_path, capsys):
        """A real failure on an existing project still explains the outcome."""
        target = tmp_path / "proj"
        (target / ".osh").mkdir(parents=True)
        (target / ".osh" / "keep.txt").touch()

        with pytest.raises(click.ClickException, match="boom"):
            with init_cmd._rollback_new_osh_dir(target):
                raise click.ClickException("boom")

        assert "left untouched" in capsys.readouterr().err

    def test_failed_base_init_removes_new_osh_dir(self, tmp_path, monkeypatch):
        """``osh init`` failing after creating ``.osh`` removes it."""
        target = tmp_path / "fresh"
        target.mkdir()
        (target / ".git").mkdir()
        monkeypatch.setattr(
            "osh.commands.init_cmd.setup_project_neutralize_scripts",
            _raise_backend_init_error,
        )

        result = CliRunner().invoke(
            main, ["init", "19.0", "--edition", "ce", str(target)]
        )

        assert result.exit_code != 0
        assert not (target / ".osh").exists()


class TestProjectDiscovery:
    """Helpers locating Osh projects around or below a directory."""

    def test_find_enclosing_project(self, tmp_path):
        parent = tmp_path / "parent"
        (parent / ".osh").mkdir(parents=True)
        child = parent / "a" / "b"
        child.mkdir(parents=True)

        assert find_enclosing_project(child) == parent.resolve()

    def test_find_enclosing_project_ignores_own_osh(self, tmp_path):
        """A project's own ``.osh`` does not count as enclosing (re-init)."""
        parent = tmp_path / "parent"
        (parent / ".osh").mkdir(parents=True)

        assert find_enclosing_project(parent) is None

    def test_find_enclosing_project_none(self, tmp_path):
        assert find_enclosing_project(tmp_path / "missing") is None

    def test_find_enclosing_project_stops_at_home(self, tmp_path, monkeypatch):
        """A ``.osh`` above ``$HOME`` is never enclosing for ``$HOME``."""
        (tmp_path / ".osh").mkdir()
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))

        assert find_enclosing_project(home) is None
        assert find_enclosing_project(home / "sub") is None

    def test_find_nested_projects(self, tmp_path):
        base = tmp_path / "base"
        (base / "child" / ".osh").mkdir(parents=True)
        (base / "deep" / "sub" / ".osh").mkdir(parents=True)
        (base / ".hidden" / ".osh").mkdir(parents=True)
        repo = base / "repo"
        (repo / ".git").mkdir(parents=True)
        (repo / ".git" / "HEAD").touch()
        (repo / "sub" / ".osh").mkdir(parents=True)

        assert find_nested_projects(base) == [
            base / "child",
            base / "deep" / "sub",
        ]

    def test_find_nested_projects_missing_base(self, tmp_path):
        """A nonexistent base has no nested projects (and cannot be listed)."""
        assert find_nested_projects(tmp_path / "missing") == []

    def test_find_nested_projects_git_repo_base(self, tmp_path):
        """A git repository root is never descended into, even as the base."""
        base = tmp_path / "repo"
        (base / ".git").mkdir(parents=True)
        (base / ".git" / "HEAD").touch()
        (base / "sub" / ".osh").mkdir(parents=True)

        assert find_nested_projects(base) == []

    def test_find_nested_projects_skips_unreadable(self, tmp_path, monkeypatch):
        """Directories that cannot be listed are skipped, not fatal."""
        base = tmp_path / "base"
        (base / "child" / ".osh").mkdir(parents=True)
        (base / "blocked").mkdir(parents=True)
        original_iterdir = Path.iterdir

        def fake_iterdir(self):
            if self == base / "blocked":
                raise PermissionError("denied")
            return original_iterdir(self)

        monkeypatch.setattr(Path, "iterdir", fake_iterdir)

        assert find_nested_projects(base) == [base / "child"]


class TestNestedInit:
    """``osh init`` guards against accidental nested environments."""

    def _make_parent(self, tmp_path):
        parent = tmp_path / "parent"
        (parent / ".osh").mkdir(parents=True)
        return parent

    def test_init_inside_project_aborts_on_decline(self, tmp_path):
        parent = self._make_parent(tmp_path)
        child = parent / "child"
        (child / ".git").mkdir(parents=True)

        result = CliRunner().invoke(
            main, ["init", "19.0", "--edition", "ce", str(child)], input="n\n"
        )

        assert result.exit_code != 0
        assert "inside the Osh project" in result.output
        assert not (child / ".osh").exists()

    def test_init_inside_project_records_parent(self, tmp_path):
        parent = self._make_parent(tmp_path)
        child = parent / "child"
        (child / ".git").mkdir(parents=True)

        result = CliRunner().invoke(
            main, ["init", "19.0", "--edition", "ce", str(child)], input="y\n"
        )

        assert result.exit_code == 0, result.output
        assert (child / ".osh").is_dir()
        # The parent is recorded relative to the child so the config
        # survives the checkout being moved.
        assert get_project_config(child, "init", "parent") == ".."
        assert get_init_parent(child) == parent.resolve()

    def test_init_inside_project_assume_yes(self, tmp_path):
        parent = self._make_parent(tmp_path)
        child = parent / "child"
        (child / ".git").mkdir(parents=True)

        result = CliRunner().invoke(
            main, ["init", "19.0", "--edition", "ce", "--yes", str(child)]
        )

        assert result.exit_code == 0, result.output
        assert get_init_parent(child) == parent.resolve()

    def test_reinit_acknowledged_nested_skips_prompt(self, tmp_path):
        """A nested project with a recorded parent re-inits without asking."""
        parent = self._make_parent(tmp_path)
        child = parent / "child"
        (child / ".git").mkdir(parents=True)
        (child / ".osh").mkdir()
        set_project_config(child, "init", "parent", str(parent.resolve()))

        result = CliRunner().invoke(
            main, ["init", "19.0", "--edition", "ce", str(child)]
        )

        assert result.exit_code == 0, result.output
        assert "Create a nested Osh project?" not in result.output

    def test_init_inside_env_dir_rejected(self, tmp_path):
        """Initialising inside the parent's ``.osh/`` is never legitimate."""
        parent = self._make_parent(tmp_path)
        env_sub = parent / ".osh" / "odoo"
        (env_sub / ".git").mkdir(parents=True)

        result = CliRunner().invoke(
            main, ["init", "19.0", "--edition", "ce", str(env_sub)]
        )

        assert result.exit_code != 0
        assert "environment directory" in result.output
        assert not (env_sub / ".osh").exists()

    def test_init_inside_env_dir_leaves_no_directories(self, tmp_path):
        """The env-dir rejection runs before the target directory is created."""
        parent = self._make_parent(tmp_path)
        env_sub = parent / ".osh" / "newdir"

        result = CliRunner().invoke(
            main, ["init", "19.0", "--edition", "ce", str(env_sub)]
        )

        assert result.exit_code != 0
        assert "environment directory" in result.output
        assert not env_sub.exists()

    def test_init_inside_symlinked_env_dir_rejected(self, tmp_path):
        """A symlinked parent ``.osh`` is resolved for the env-dir check."""
        parent = tmp_path / "parent"
        real_env = parent / "env"
        real_env.mkdir(parents=True)
        (parent / ".osh").symlink_to(real_env, target_is_directory=True)
        env_sub = real_env / "odoo"
        env_sub.mkdir()

        result = CliRunner().invoke(
            main,
            ["init", "19.0", "--edition", "ce", str(parent / ".osh" / "odoo")],
        )

        assert result.exit_code != 0
        assert "environment directory" in result.output

    def test_dry_run_creates_nothing(self, tmp_path):
        """``--dry-run`` performs no filesystem changes."""
        target = tmp_path / "newproject"

        result = CliRunner().invoke(
            main, ["init", "19.0", "--edition", "ce", "--dry-run", str(target)]
        )

        assert result.exit_code == 0, result.output
        assert not target.exists()

    def test_dry_run_nested_does_not_prompt(self, tmp_path):
        """``--dry-run`` reports nesting but asks nothing and writes nothing."""
        parent = self._make_parent(tmp_path)
        child = parent / "child"
        (child / ".git").mkdir(parents=True)

        result = CliRunner().invoke(
            main,
            ["init", "19.0", "--edition", "ce", "--dry-run", str(child)],
        )

        assert result.exit_code == 0, result.output
        assert "inside the Osh project" in result.output
        assert not (child / ".osh").exists()

    def test_init_warns_on_nested_projects_below(self, tmp_path):
        workspace = tmp_path / "workspace"
        (workspace / ".git").mkdir(parents=True)
        (workspace / "sub" / ".osh").mkdir(parents=True)

        result = CliRunner().invoke(
            main, ["init", "19.0", "--edition", "ce", str(workspace)]
        )

        assert result.exit_code == 0, result.output
        assert "Existing Osh project(s)" in result.output

    def test_init_at_home_requires_confirmation(self, tmp_path, monkeypatch):
        """A ``~/.osh`` would shadow every project-less directory under it."""
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / ".git").mkdir()

        result = CliRunner().invoke(
            main, ["init", "19.0", "--edition", "ce", str(tmp_path)], input="n\n"
        )

        assert result.exit_code != 0
        assert "home directory" in result.output
        assert not (tmp_path / ".osh").exists()

    def test_init_clears_stale_parent(self, tmp_path):
        """Re-init outside the former parent removes the recorded parent."""
        project = tmp_path / "project"
        (project / ".git").mkdir(parents=True)
        (project / ".osh").mkdir()
        set_project_config(project, "init", "parent", "/somewhere")

        result = CliRunner().invoke(
            main, ["init", "19.0", "--edition", "ce", str(project)]
        )

        assert result.exit_code == 0, result.output
        assert get_project_config(project, "init", "parent") is None

    def test_reinit_relative_parent_skips_prompt(self, tmp_path):
        """A relative recorded parent acknowledges the nesting."""
        parent = self._make_parent(tmp_path)
        child = parent / "child"
        (child / ".git").mkdir(parents=True)
        (child / ".osh").mkdir()
        set_project_config(child, "init", "parent", "..")

        result = CliRunner().invoke(
            main, ["init", "19.0", "--edition", "ce", str(child)]
        )

        assert result.exit_code == 0, result.output
        assert "Create a nested Osh project?" not in result.output


class TestGetInitParent:
    """``init.parent`` resolution in ``.osh/config.toml``."""

    def test_relative_parent(self, tmp_path):
        child = tmp_path / "parent" / "child"
        child.mkdir(parents=True)
        set_project_config(child, "init", "parent", "..")

        assert get_init_parent(child) == (tmp_path / "parent").resolve()

    def test_legacy_absolute_parent(self, tmp_path):
        """Absolute paths written by older versions still resolve."""
        child = tmp_path / "parent" / "child"
        child.mkdir(parents=True)
        set_project_config(child, "init", "parent", str(tmp_path / "parent"))

        assert get_init_parent(child) == (tmp_path / "parent").resolve()

    def test_no_parent(self, tmp_path):
        assert get_init_parent(tmp_path) is None
