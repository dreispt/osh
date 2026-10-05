"""Tests for the ``osh addon`` command group."""

import importlib
import shutil
import subprocess

import click
import pytest
from click.testing import CliRunner


@pytest.fixture(autouse=True)
def _reset_cli():
    """Reload osh.cli after each test so imports return to the default state."""
    yield
    from osh import cli

    importlib.reload(cli)


def _osh(*args):
    """Invoke the ``osh`` CLI — ``main`` is re-read to survive the reload fixture."""
    from osh.cli import main

    return CliRunner().invoke(main, list(args))


def _make_addons_dir(path, module="some_module"):
    """Create an addons directory holding one module at *path*."""
    mod = path / module
    mod.mkdir(parents=True)
    (mod / "__manifest__.py").write_text("{'name': 'Some Module'}\n")
    return path


def _make_addons_repo(path, branch="19.0", module="some_module"):
    """Create a real git addons repository at *path* on *branch*."""
    _make_addons_dir(path, module)

    def git(*args):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)

    git("init", "-b", branch)
    git("add", ".")
    git(
        "-c",
        "user.email=test@example.com",
        "-c",
        "user.name=Test",
        "commit",
        "-m",
        "add module",
    )
    return path


def test_addon_is_a_command_group():
    """``addon`` is registered as a group on the root CLI."""
    from osh.cli import main

    assert isinstance(main.commands["addon"], click.Group)


def test_addon_group_accepts_plugin_subcommands(monkeypatch):
    """Plugin ``group_commands`` entries attach under ``osh addon``."""

    @click.command(name="audit")
    def plugin_audit():
        click.echo("plugin audit")

    monkeypatch.setattr(
        "osh.utils.plugin_loader.load_group_commands",
        lambda: {"addon": [("fake", plugin_audit)]},
    )

    from osh import cli

    importlib.reload(cli)

    assert cli.main.commands["addon"].commands["audit"].callback is (
        plugin_audit.callback
    )

    result = CliRunner().invoke(cli.main, ["addon", "--help"])
    assert result.exit_code == 0
    core_section, _, plugin_section = result.output.partition("Plugin Commands:")
    assert "audit" not in core_section
    assert "audit" in plugin_section
    assert "[fake]" in plugin_section


def test_addon_path_add_local_dir(in_project, tmp_path):
    """``addon path add`` registers a local addons directory."""
    dep = _make_addons_dir(tmp_path / "payroll")

    result = _osh("addon", "path", "add", str(dep))
    assert result.exit_code == 0, result.output

    from osh.config import get_project_config
    from osh.utils.odoo_layout import build_addons_paths

    assert get_project_config(in_project, "addons", "paths") == [str(dep)]
    assert dep in build_addons_paths(in_project)


def test_addon_path_add_module_dir_registers_parent(in_project, tmp_path):
    """Adding a module directory registers its parent addons directory."""
    dep = _make_addons_dir(tmp_path / "payroll")

    result = _osh("addon", "path", "add", str(dep / "some_module"))
    assert result.exit_code == 0, result.output

    from osh.config import get_project_config

    assert get_project_config(in_project, "addons", "paths") == [str(dep)]


def test_addon_path_add_git_url_clones_into_osh(in_project, tmp_path):
    """A git URL is shallow-cloned into ``.osh/<name>`` on the project version."""
    from osh.config import get_project_config, set_project_config

    repo = _make_addons_repo(tmp_path / "payroll-src", branch="19.0")
    set_project_config(in_project, "init", "version", "19.0")

    result = _osh("addon", "path", "add", f"file://{repo}")
    assert result.exit_code == 0, result.output

    clone = in_project / ".osh" / "payroll-src"
    assert (clone / "some_module" / "__manifest__.py").exists()
    assert get_project_config(in_project, "addons", "paths") == [".osh/payroll-src"]


def test_addon_path_remove(in_project, tmp_path):
    """``addon path remove`` unregisters by path or directory name."""
    dep = _make_addons_dir(tmp_path / "payroll")
    other = _make_addons_dir(tmp_path / "l10n-misc")
    _osh("addon", "path", "add", str(dep))
    _osh("addon", "path", "add", str(other))

    from osh.config import get_project_config

    result = _osh("addon", "path", "remove", "payroll")
    assert result.exit_code == 0, result.output
    assert get_project_config(in_project, "addons", "paths") == [str(other)]
    assert dep.exists()

    result = _osh("addon", "path", "remove", str(other))
    assert result.exit_code == 0, result.output
    assert get_project_config(in_project, "addons", "paths") == []
    assert other.exists()

    result = _osh("addon", "path", "remove", "payroll")
    assert result.exit_code != 0


def test_addon_path_list(in_project, tmp_path):
    """``addon path list`` shows each registered path and its status."""
    dep = _make_addons_dir(tmp_path / "payroll")
    _osh("addon", "path", "add", str(dep))

    result = _osh("addon", "path", "list")
    assert result.exit_code == 0, result.output
    assert str(dep) in result.output
    assert "1 module" in result.output


def test_addon_path_add_git_url_with_name(in_project, tmp_path):
    """``--name`` overrides the directory name derived from the URL."""
    repo = _make_addons_repo(tmp_path / "payroll-src")

    result = _osh("addon", "path", "add", f"file://{repo}", "--name", "payroll-dep")
    assert result.exit_code == 0, result.output

    from osh.config import get_project_config

    clone = in_project / ".osh" / "payroll-dep"
    assert (clone / "some_module" / "__manifest__.py").exists()
    assert get_project_config(in_project, "addons", "paths") == [".osh/payroll-dep"]


def test_addon_path_add_rejects_options_on_local_dir(in_project, tmp_path):
    """``--name`` and ``--branch`` are refused for local directories."""
    dep = _make_addons_dir(tmp_path / "payroll")

    result = _osh("addon", "path", "add", str(dep), "--name", "payroll-dep")
    assert result.exit_code != 0
    assert "--name and --branch only apply to git sources" in result.output

    result = _osh("addon", "path", "add", str(dep), "--branch", "19.0")
    assert result.exit_code != 0


def test_addon_path_add_rejects_traversal_name(in_project, tmp_path):
    """``--name`` cannot make the clone escape the ``.osh`` directory."""
    repo = _make_addons_repo(tmp_path / "payroll-src")

    for name in ("../escaped", "..", "nested/dir"):
        result = _osh("addon", "path", "add", f"file://{repo}", "--name", name)
        assert result.exit_code != 0, name
        assert not (in_project.parent / "escaped").exists()
        assert not (in_project / ".osh" / "nested").exists()


def test_addon_path_add_shorthand_clones_github_url(in_project, monkeypatch):
    """An ``owner/repo`` source clones from GitHub into ``.osh/<repo>``."""
    cloned = []

    def fake_clone(url, target, **kwargs):
        cloned.append(url)
        _make_addons_dir(target)

    monkeypatch.setattr("osh.commands.addon_cmd._git_shallow_clone", fake_clone)

    result = _osh("addon", "path", "add", "oca/payroll")
    assert result.exit_code == 0, result.output
    assert cloned == ["https://github.com/oca/payroll.git"]

    from osh.config import get_project_config

    assert get_project_config(in_project, "addons", "paths") == [".osh/payroll"]


def test_addon_path_missing_dir_warns(in_project, tmp_path, capsys):
    """A registered path that no longer exists warns and is skipped."""
    dep = _make_addons_dir(tmp_path / "payroll")
    _osh("addon", "path", "add", str(dep))
    shutil.rmtree(tmp_path / "payroll")

    from osh import echo
    from osh.utils.odoo_layout import build_addons_paths

    echo.set_config(verbosity="normal", base=in_project)
    paths = build_addons_paths(in_project)
    assert dep not in paths
    captured = capsys.readouterr()
    assert "does not exist" in captured.out + captured.err
