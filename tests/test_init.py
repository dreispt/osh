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
from osh.common import (
    find_enclosing_project,
    find_nested_projects,
    setup_project_neutralize_scripts,
)
from osh.config import get_init_parent
from osh.db import get_project_config, set_project_config
from osh.plugins.osh_runtime_venv.runtimes import VenvRuntime
from osh.sources import (
    DEFAULT_ODOO_URL,
    SOURCE_NAMES,
    _cache_has_branch,
    _ensure_repo_cache,
    _find_local_source,
    _install_source_plan,
    _is_git_url,
    _resolve_source,
    _source_branch,
    ensure_osh_sources,
    parse_source_pins,
    update_osh_sources,
)

from .helpers import git, make_bare_repo, make_worktree_project


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


LOCAL_SOURCES = (Path(__file__).parent / "data" / "local_sources").resolve()


class TestFindLocalSources:
    """Detection against the checked-in trees in ``tests/data/local_sources``.

    Each scenario needs its own root: exact-name matching takes precedence
    over globs, so e.g. a real ``enterprise/`` dir would shadow the
    ``*enterprise*`` pattern test if they shared a tree.
    """

    def test_find_odoo_in_root(self):
        base = LOCAL_SOURCES / "root_odoo"
        assert _find_local_source(base, ("",), ("odoo-bin",)) == (base, False)

    def test_find_odoo_in_subdirectory(self):
        base = LOCAL_SOURCES / "nested_odoo"
        assert _find_local_source(base, ("",), ("odoo-bin",)) == (
            base / "odoo",
            False,
        )

    def test_find_enterprise_with_manifest(self):
        base = LOCAL_SOURCES / "enterprise_dir"
        assert _find_local_source(
            base, ("enterprise",), ("*/__manifest__.py", "*/__openerp__.py")
        ) == (base / "enterprise", False)

    def test_find_themes_with_manifest(self):
        base = LOCAL_SOURCES / "themes_dir"
        assert _find_local_source(
            base,
            ("design-themes", "themes"),
            ("*/__manifest__.py", "*/__openerp__.py"),
        ) == (base / "design-themes", False)

    def test_find_enterprise_with_pattern(self):
        base = LOCAL_SOURCES / "glob_enterprise"
        path, requires_confirmation = _find_local_source(
            base,
            ("enterprise", "enterprise-copy", "*enterprise*"),
            ("*/__manifest__.py", "*/__openerp__.py"),
        )
        assert path == base / "my-enterprise-dir"
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
        bare = make_bare_repo(tmp_path, "odoo")
        cache = _ensure_repo_cache("odoo", "master", f"file://{bare}")

        assert cache == patch_cache / "odoo.git"
        assert cache.exists()
        assert (cache / "config").exists()
        assert _cache_has_branch(cache, "master")

    def test_fetches_missing_version(self, tmp_path, patch_cache):
        bare = make_bare_repo(tmp_path, "odoo", ("master", "19.0"))
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
        bare = make_bare_repo(tmp_path, "odoo")
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
        bare = make_bare_repo(tmp_path, "odoo")
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


class TestInitVersion:
    """``osh init`` without a VERSION argument reuses recorded defaults."""

    def test_reinit_without_version_uses_recorded(self, tmp_project):
        """The recorded ``init.version`` is reused when VERSION is omitted."""
        set_project_config(tmp_project, "init", "version", "18.0")

        result = CliRunner().invoke(main, ["init", "--edition", "ce", str(tmp_project)])

        assert result.exit_code == 0, result.output
        assert "Odoo 18.0" in result.output
        assert get_project_config(tmp_project, "init", "version") == "18.0"
        # A bare re-init is how a project picks up newly bundled defaults.
        assert (tmp_project / ".osh" / "neutralize" / "900_clear_assets.sql").exists()

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

    def test_user_config_sets_default_version(self, tmp_project, user_config):
        """``[init] version`` in the user config supplies a default."""
        user_config.parent.mkdir(parents=True)
        user_config.write_text('[init]\nversion = "19.0"\n')

        result = CliRunner().invoke(main, ["init", "--edition", "ce", str(tmp_project)])

        assert result.exit_code == 0, result.output
        assert get_project_config(tmp_project, "init", "version") == "19.0"

    def test_recorded_version_beats_user_default(self, tmp_project, user_config):
        """A recorded project version is never overridden by a user default."""
        user_config.parent.mkdir(parents=True)
        user_config.write_text('[init]\nversion = "19.0"\n')
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
            main,
            ["init", "--edition", "ce", str(tmp_project)],
            input="17.0\nhost\ny\n",
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

    def test_odoo_sources_detect_version(self, tmp_project):
        """A checked-out ``odoo/release.py`` is the strongest version hint."""
        release = tmp_project / "odoo" / "odoo" / "release.py"
        release.parent.mkdir(parents=True)
        release.write_text('version = "18.0"\n')
        # The checkout wins over weaker signals.
        (tmp_project / "repos.yml").write_text("merges:\n    - oca 19.0\n")

        result = CliRunner().invoke(main, ["init", "--edition", "ce", str(tmp_project)])

        assert result.exit_code == 0, result.output
        assert "Detected Odoo 18.0" in result.output
        assert get_project_config(tmp_project, "init", "version") == "18.0"

    def test_repos_yml_detects_version(self, tmp_project):
        """Git-aggregator ``repos.yml`` merge refs pin the Odoo version."""
        (tmp_project / "repos.yml").write_text(
            "./account-invoicing:\n"
            "  remotes:\n"
            "    oca: https://github.com/OCA/account-invoicing.git\n"
            "  merges:\n"
            "    - oca 19.0\n"
            "  target: oca gift/19.0\n"
        )

        result = CliRunner().invoke(main, ["init", "--edition", "ce", str(tmp_project)])

        assert result.exit_code == 0, result.output
        assert "Detected Odoo 19.0" in result.output
        assert get_project_config(tmp_project, "init", "version") == "19.0"

    def test_addon_manifest_detects_version(self, tmp_project):
        """An addon's manifest version pin (``19.0.1.0``) is a hint."""
        addon = tmp_project / "addons" / "my_addon"
        addon.mkdir(parents=True)
        (addon / "__manifest__.py").write_text(
            "{'name': 'My Addon', 'version': '19.0.1.0'}\n"
        )

        result = CliRunner().invoke(main, ["init", "--edition", "ce", str(tmp_project)])

        assert result.exit_code == 0, result.output
        assert "Detected Odoo 19.0" in result.output
        assert get_project_config(tmp_project, "init", "version") == "19.0"

    def test_branch_name_detects_version(self, tmp_project):
        """A ``staging-19``-style branch name is an Odoo version hint."""
        (tmp_project / ".git" / "HEAD").write_text("ref: refs/heads/staging-19\n")

        result = CliRunner().invoke(main, ["init", "--edition", "ce", str(tmp_project)])

        assert result.exit_code == 0, result.output
        assert "Detected Odoo 19.0" in result.output
        assert get_project_config(tmp_project, "init", "version") == "19.0"

    def test_detected_version_is_prompt_default(self, tmp_project, monkeypatch):
        """The detected version pre-fills the interactive prompt."""
        (tmp_project / "repos.yml").write_text("merges:\n    - oca 19.0\n")
        monkeypatch.setattr(
            "click.testing._NamedTextIOWrapper.isatty", lambda self: True
        )

        result = CliRunner().invoke(
            main,
            ["init", "--edition", "ce", str(tmp_project)],
            input="\nhost\ny\n",
        )

        assert result.exit_code == 0, result.output
        assert "Odoo version" in result.output
        assert get_project_config(tmp_project, "init", "version") == "19.0"


class TestNeutralizeScripts:
    """``.osh/neutralize`` seeding with bundled and user scripts."""

    @pytest.fixture
    def user_neutralize_dir(self, tmp_path, monkeypatch):
        """Redirect ``~/.config/osh/neutralize`` to a temporary directory."""
        user_dir = tmp_path / "user-neutralize"
        monkeypatch.setattr("osh.common.get_user_neutralize_dir", lambda: user_dir)
        return user_dir

    def test_clear_assets_installed_for_current_versions(
        self, tmp_project, user_neutralize_dir
    ):
        """The bundled asset cleanup runs after neutralization on any version."""
        setup_project_neutralize_scripts(tmp_project, "19.0")

        script = tmp_project / ".osh" / "neutralize" / "900_clear_assets.sql"
        assert script.exists()
        assert "DELETE FROM ir_attachment" in script.read_text()
        # Only generated bundles are removed, never user attachments.
        assert "res_model = 'ir.ui.view'" in script.read_text()
        assert not (
            tmp_project / ".osh" / "neutralize" / "000_osh_default.sql"
        ).exists()

    def test_fallback_still_limited_to_old_versions(
        self, tmp_project, user_neutralize_dir
    ):
        """Odoo < 16 additionally gets the SQL neutralization fallback."""
        setup_project_neutralize_scripts(tmp_project, "15.0")

        neutralize_dir = tmp_project / ".osh" / "neutralize"
        assert (neutralize_dir / "000_osh_default.sql").exists()
        assert (neutralize_dir / "900_clear_assets.sql").exists()

    def test_stale_project_script_is_refreshed(self, tmp_project, user_neutralize_dir):
        """A stale project copy is replaced so script fixes reach re-inits."""
        neutralize_dir = tmp_project / ".osh" / "neutralize"
        neutralize_dir.mkdir(parents=True)
        (neutralize_dir / "900_clear_assets.sql").write_text("-- stale\n")

        setup_project_neutralize_scripts(tmp_project, "19.0")

        script = neutralize_dir / "900_clear_assets.sql"
        assert "DELETE FROM ir_attachment" in script.read_text()

    def test_user_script_wins_over_bundled_default(
        self, tmp_project, user_neutralize_dir
    ):
        """A same-named user script still overrides the bundled default."""
        user_neutralize_dir.mkdir(parents=True)
        (user_neutralize_dir / "900_clear_assets.sql").write_text("-- mine\n")

        setup_project_neutralize_scripts(tmp_project, "19.0")

        script = tmp_project / ".osh" / "neutralize" / "900_clear_assets.sql"
        assert script.read_text() == "-- mine\n"


class TestSourceVersionSwitching:
    def test_managed_source_is_replaced_for_a_different_version(
        self, tmp_path, tmp_project, patch_cache, monkeypatch
    ):
        """Re-running ``osh init`` with a different version replaces managed sources."""
        bare = make_bare_repo(tmp_path, "odoo", ("master", "19.0"))
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


def _raise_runtime_init_error(*args, **kwargs):
    raise click.ClickException("runtime init failed")


class TestInitRollback:
    """A failed or aborted init must not leave a stale ``.osh`` marker."""

    def test_failed_runtime_init_removes_new_osh_dir(self, tmp_path, monkeypatch):
        """``osh init --runtime=venv`` failure removes the ``.osh`` it created."""
        target = tmp_path / "fresh"
        target.mkdir()
        (target / ".git").mkdir()
        monkeypatch.setattr(VenvRuntime, "init", _raise_runtime_init_error)

        result = CliRunner().invoke(
            main,
            ["init", "19.0", str(target), "--edition", "ce", "--runtime", "venv"],
        )

        assert result.exit_code != 0
        assert not (target / ".osh").exists()

    def test_failed_runtime_init_keeps_existing_osh_dir(self, tmp_project, monkeypatch):
        """A failed re-init never removes a ``.osh`` holding existing state."""
        marker = tmp_project / ".osh" / "keep.txt"
        marker.write_text("keep")
        monkeypatch.setattr(VenvRuntime, "init", _raise_runtime_init_error)

        result = CliRunner().invoke(
            main,
            [
                "init",
                "19.0",
                str(tmp_project),
                "--edition",
                "ce",
                "--runtime",
                "venv",
            ],
        )

        assert result.exit_code != 0
        assert marker.exists()

    def test_failed_runtime_init_removes_empty_osh_dir(self, tmp_project, monkeypatch):
        """An empty ``.osh`` holds no state and is removed on failure."""
        monkeypatch.setattr(VenvRuntime, "init", _raise_runtime_init_error)

        result = CliRunner().invoke(
            main,
            [
                "init",
                "19.0",
                str(tmp_project),
                "--edition",
                "ce",
                "--runtime",
                "venv",
            ],
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
                init_cmd.run_runtime_init(
                    ctx,
                    VenvRuntime(),
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
            _raise_runtime_init_error,
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


def _rev_parse(repo, rev="HEAD"):
    """Return the sha *rev* resolves to in *repo*."""
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", rev],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _show_branch(repo):
    """Return the checked-out branch in *repo* ('' when detached)."""
    return subprocess.run(
        ["git", "-C", str(repo), "branch", "--show-current"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class TestSourcePins:
    """``parse_source_pins`` turns ``--ref``/``--date`` values into pins."""

    def test_bare_ref_applies_to_all_sources(self):
        pins = parse_source_pins(("19.0",), ())
        assert pins == {name: ("rev", "19.0") for name in SOURCE_NAMES}

    def test_full_hex_ref_is_a_sha(self):
        sha = "a" * 40
        pins = parse_source_pins((f"odoo={sha}",), ())
        assert pins == {"odoo": ("sha", sha)}

    def test_scoped_ref(self):
        pins = parse_source_pins(("enterprise=19.0",), ())
        assert pins == {"enterprise": ("rev", "19.0")}

    def test_scoped_date(self):
        pins = parse_source_pins((), ("odoo=2026-10-15",))
        assert pins == {"odoo": ("date", "2026-10-15")}

    def test_unknown_source_name(self):
        with pytest.raises(click.ClickException, match="Unknown source 'foo'"):
            parse_source_pins(("foo=19.0",), ())

    def test_empty_value(self):
        with pytest.raises(click.ClickException, match="Empty pin"):
            parse_source_pins(("odoo=",), ())

    def test_conflicting_pins(self):
        with pytest.raises(click.ClickException, match="Conflicting"):
            parse_source_pins(("odoo=19.0", "odoo=master"), ())
        # A bare --ref and a bare --date cover the same sources
        with pytest.raises(click.ClickException, match="Conflicting"):
            parse_source_pins(("19.0",), ("2026-10-15",))


class TestUpdateSources:
    """``update_osh_sources`` refreshes cache refs and managed clones.

    ``make_bare_repo`` leaves the work tree at ``tmp_path / "odoo"`` and
    the bare remote at ``tmp_path / "odoo.git"``; commits pushed from the
    work tree to the bare repo model upstream changes.
    """

    def _install(self, tmp_project, bare, branch="master"):
        """Clone *bare* into the project's ``.osh/odoo`` via the cache."""
        return _install_source_plan(
            "odoo", branch, "cache", f"file://{bare}", tmp_project / ".osh"
        )

    def _push_commit(self, tmp_path, bare, branch="master", date=None):
        """Commit on *branch* in the work tree and push it to *bare*."""
        work = tmp_path / "odoo"
        marker = work / f"commit-{len(list(work.iterdir()))}"
        marker.write_text("x")
        git(work, "add", "-A")
        env = {"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date} if date else None
        git(work, "commit", "-m", marker.name, env=env)
        git(work, "push", str(bare), f"HEAD:{branch}")
        return _rev_parse(work)

    def test_updates_clone_and_cache(self, tmp_path, tmp_project, patch_cache):
        bare = make_bare_repo(tmp_path, "odoo")
        link = self._install(tmp_project, bare)
        old = _rev_parse(link)

        pushed = self._push_commit(tmp_path, bare)
        assert pushed != old

        update_osh_sources(tmp_project, version="master")

        assert _rev_parse(link) == pushed
        assert _rev_parse(patch_cache / "odoo.git", "master") == pushed

    def test_already_up_to_date(self, tmp_path, tmp_project, patch_cache):
        bare = make_bare_repo(tmp_path, "odoo")
        link = self._install(tmp_project, bare)
        head = _rev_parse(link)

        update_osh_sources(tmp_project, version="master")

        assert _rev_parse(link) == head
        assert _show_branch(link) == "master"

    def test_skips_linked_source(self, tmp_path, tmp_project, patch_cache):
        src = tmp_path / "odoo-src"
        src.mkdir()
        (src / "odoo-bin").touch()
        (tmp_project / ".osh" / "odoo").symlink_to(src)

        update_osh_sources(tmp_project, version="master")

        assert (tmp_project / ".osh" / "odoo").is_symlink()
        assert not (src / ".git").exists()

    def test_dirty_clone_skipped_unless_forced(
        self, tmp_path, tmp_project, patch_cache
    ):
        bare = make_bare_repo(tmp_path, "odoo")
        link = self._install(tmp_project, bare)
        old = _rev_parse(link)
        pushed = self._push_commit(tmp_path, bare)
        original = (link / "README").read_text()
        (link / "README").write_text("dirty")

        update_osh_sources(tmp_project, version="master")
        assert _rev_parse(link) == old
        assert (link / "README").read_text() == "dirty"

        update_osh_sources(tmp_project, version="master", force=True)
        assert _rev_parse(link) == pushed
        assert (link / "README").read_text() == original

    def test_pin_sha_detaches(self, tmp_path, tmp_project, patch_cache):
        bare = make_bare_repo(tmp_path, "odoo")
        link = self._install(tmp_project, bare)
        first = _rev_parse(link)
        self._push_commit(tmp_path, bare)

        update_osh_sources(tmp_project, version="master", pins={"odoo": ("sha", first)})

        assert _rev_parse(link) == first
        assert _show_branch(link) == ""

    def test_pin_sha_fetches_uncached_object(self, tmp_path, tmp_project, patch_cache):
        """A sha absent from the depth-1 cache is fetched through it."""
        bare = make_bare_repo(tmp_path, "odoo", ("master", "19.0"))
        link = self._install(tmp_project, bare)  # cache holds master only
        target = _rev_parse(bare, "19.0")

        update_osh_sources(
            tmp_project, version="master", pins={"odoo": ("sha", target)}
        )

        assert _rev_parse(link) == target
        assert _show_branch(link) == ""

    def test_pin_sha_unknown_errors(self, tmp_path, tmp_project, patch_cache):
        bare = make_bare_repo(tmp_path, "odoo")
        self._install(tmp_project, bare)

        with pytest.raises(click.ClickException, match="Could not fetch"):
            update_osh_sources(
                tmp_project,
                version="master",
                pins={"odoo": ("sha", "f" * 40)},
            )

    def test_detached_clone_bad_version_hint(self, tmp_path, tmp_project, patch_cache):
        """A detached clone falls back to the recorded version; a bogus
        one is flagged as a config problem, not a git error."""
        bare = make_bare_repo(tmp_path, "odoo")
        link = self._install(tmp_project, bare)
        git(link, "switch", "--detach", _rev_parse(link))

        with pytest.raises(click.ClickException, match="recorded init version"):
            update_osh_sources(tmp_project, version="bogus-branch")

    def test_pin_branch_switches(self, tmp_path, tmp_project, patch_cache):
        bare = make_bare_repo(tmp_path, "odoo", ("master", "19.0"))
        link = self._install(tmp_project, bare)

        update_osh_sources(
            tmp_project, version="master", pins={"odoo": ("rev", "19.0")}
        )

        assert _show_branch(link) == "19.0"
        assert _rev_parse(link) == _rev_parse(bare, "19.0")

    def test_pin_tag_detaches(self, tmp_path, tmp_project, patch_cache):
        bare = make_bare_repo(tmp_path, "odoo")
        work = tmp_path / "odoo"
        git(work, "tag", "v1.0")
        git(work, "push", str(bare), "v1.0")
        link = self._install(tmp_project, bare)
        target = _rev_parse(bare, "v1.0")
        self._push_commit(tmp_path, bare)

        update_osh_sources(
            tmp_project, version="master", pins={"odoo": ("rev", "v1.0")}
        )

        assert _rev_parse(link) == target
        assert _show_branch(link) == ""

    def test_pin_date_resolves_asof_commit(self, tmp_path, tmp_project, patch_cache):
        bare = make_bare_repo(tmp_path, "odoo")
        link = self._install(tmp_project, bare)
        first = self._push_commit(tmp_path, bare, date="2026-10-05T10:00:00")
        self._push_commit(tmp_path, bare, date="2026-10-15T10:00:00")

        update_osh_sources(
            tmp_project,
            version="master",
            pins={"odoo": ("date", "2026-10-10")},
        )

        assert _rev_parse(link) == first

    def test_worktree_updates_env_root(self, tmp_path, patch_cache):
        """A linked worktree updates the source repository's .osh clones."""
        project, worktree = make_worktree_project(tmp_path)
        bare = make_bare_repo(tmp_path, "odoo")
        link = _install_source_plan(
            "odoo", "master", "cache", f"file://{bare}", project / ".osh"
        )
        pushed = self._push_commit(tmp_path, bare)

        update_osh_sources(worktree, version="master")

        assert _rev_parse(link) == pushed

    def test_global_mode_refreshes_cached_refs(self, tmp_path, patch_cache):
        bare = make_bare_repo(tmp_path, "odoo")
        _ensure_repo_cache("odoo", "master", f"file://{bare}")
        pushed = self._push_commit(tmp_path, bare)

        update_osh_sources(None)

        assert _rev_parse(patch_cache / "odoo.git", "master") == pushed

    def test_dry_run_changes_nothing(self, tmp_path, tmp_project, patch_cache):
        bare = make_bare_repo(tmp_path, "odoo")
        link = self._install(tmp_project, bare)
        self._push_commit(tmp_path, bare)
        old = _rev_parse(link)

        update_osh_sources(tmp_project, version="master", dry_run=True)

        assert _rev_parse(link) == old
        assert _rev_parse(patch_cache / "odoo.git", "master") == old

    def test_fresh_clone_honours_pin(self, tmp_path, tmp_project, patch_cache):
        bare = make_bare_repo(tmp_path, "odoo")
        first = _rev_parse(tmp_path / "odoo")
        self._push_commit(tmp_path, bare)

        ensure_osh_sources(
            tmp_project,
            "master",
            "ce",
            odoo_source=f"file://{bare}",
            pins={"odoo": ("sha", first)},
        )

        link = tmp_project / ".osh" / "odoo"
        assert _rev_parse(link) == first
        assert _show_branch(link) == ""


class TestUpdateSourcesCli:
    """``osh init --update-sources`` wiring."""

    def test_updates_project_sources(
        self, tmp_path, tmp_project, patch_cache, monkeypatch
    ):
        bare = make_bare_repo(tmp_path, "odoo")
        link = _install_source_plan(
            "odoo", "master", "cache", f"file://{bare}", tmp_project / ".osh"
        )
        set_project_config(tmp_project, "init", "version", "master")
        marker = tmp_path / "odoo" / "new-commit"
        marker.write_text("x")
        git(tmp_path / "odoo", "add", "-A")
        git(tmp_path / "odoo", "commit", "-m", "new")
        git(tmp_path / "odoo", "push", str(bare), "HEAD:master")
        pushed = _rev_parse(tmp_path / "odoo")
        monkeypatch.chdir(tmp_project)

        result = CliRunner().invoke(main, ["init", "--update-sources"])

        assert result.exit_code == 0, result.output
        assert _rev_parse(link) == pushed

    def test_date_flag_implies_update(
        self, tmp_path, tmp_project, patch_cache, monkeypatch
    ):
        bare = make_bare_repo(tmp_path, "odoo")
        link = _install_source_plan(
            "odoo", "master", "cache", f"file://{bare}", tmp_project / ".osh"
        )
        set_project_config(tmp_project, "init", "version", "master")
        old = _rev_parse(link)
        marker = tmp_path / "odoo" / "newer"
        marker.write_text("x")
        git(tmp_path / "odoo", "add", "-A")
        git(
            tmp_path / "odoo",
            "commit",
            "-m",
            "newer",
            env={
                "GIT_AUTHOR_DATE": "2026-10-05T10:00:00",
                "GIT_COMMITTER_DATE": "2026-10-05T10:00:00",
            },
        )
        git(tmp_path / "odoo", "push", str(bare), "HEAD:master")
        pushed = _rev_parse(tmp_path / "odoo")
        monkeypatch.chdir(tmp_project)

        result = CliRunner().invoke(main, ["init", "--date", "odoo=2026-10-10"])

        assert result.exit_code == 0, result.output
        assert _rev_parse(link) == pushed
        assert pushed != old
        assert _show_branch(link) == ""

    def test_worktree_updates_env_root_clones(self, tmp_path, patch_cache, monkeypatch):
        """``osh init --update-sources`` in a linked worktree does not fall
        into the init flow — it updates the borrowed environment."""
        project, worktree = make_worktree_project(tmp_path)
        bare = make_bare_repo(tmp_path, "odoo")
        link = _install_source_plan(
            "odoo", "master", "cache", f"file://{bare}", project / ".osh"
        )
        set_project_config(project, "init", "version", "master")
        marker = tmp_path / "odoo" / "new-commit"
        marker.write_text("x")
        git(tmp_path / "odoo", "add", "-A")
        git(tmp_path / "odoo", "commit", "-m", "new")
        git(tmp_path / "odoo", "push", str(bare), "HEAD:master")
        pushed = _rev_parse(tmp_path / "odoo")
        monkeypatch.chdir(worktree)

        result = CliRunner().invoke(main, ["init", "--update-sources"])

        assert result.exit_code == 0, result.output
        assert _rev_parse(link) == pushed
