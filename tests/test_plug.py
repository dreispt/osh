"""Tests for ``osh plug`` install/list/uninstall."""

import pytest
from click.testing import CliRunner

from osh.commands.plug_cmd import plug


@pytest.fixture
def plugin_home(tmp_path, monkeypatch):
    """Redirect the user plugin directory to a temporary location."""
    target = tmp_path / "plugins"
    monkeypatch.setattr("osh.commands.plug_cmd._user_plugin_dir", lambda: target)
    return target


@pytest.fixture
def src_plugin(tmp_path):
    """Create a minimal plugin package directory to install from."""
    src = tmp_path / "src" / "my_plugin"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("OSH_PLUGIN_MANIFEST = {}\n")
    return src


def test_install_editable_symlinks_directory(plugin_home, src_plugin):
    """``osh plug install -e`` links the directory into the plugin dir."""
    runner = CliRunner()
    result = runner.invoke(plug, ["install", "--trust", "-e", str(src_plugin)])

    assert result.exit_code == 0, result.output
    link = plugin_home / "my_plugin"
    assert link.is_symlink()
    assert link.resolve() == src_plugin.resolve()


def test_install_editable_long_option(plugin_home, src_plugin):
    """``--editable`` works as the long form."""
    runner = CliRunner()
    result = runner.invoke(plug, ["install", "--trust", "--editable", str(src_plugin)])

    assert result.exit_code == 0, result.output
    assert (plugin_home / "my_plugin").is_symlink()


def test_install_editable_requires_directory(plugin_home, tmp_path):
    """``-e`` rejects a path that is not a directory."""
    runner = CliRunner()
    result = runner.invoke(
        plug, ["install", "--trust", "-e", str(tmp_path / "missing")]
    )

    assert result.exit_code != 0
    assert "requires a local directory" in result.output


def test_install_editable_requires_plugin_package(plugin_home, tmp_path):
    """``-e`` rejects a directory without ``__init__.py``/``osh_plugin.py``."""
    empty = tmp_path / "not_a_plugin"
    empty.mkdir()
    runner = CliRunner()
    result = runner.invoke(plug, ["install", "--trust", "-e", str(empty)])

    assert result.exit_code != 0
    assert "does not look like a plugin package" in result.output


def test_install_editable_accepts_bare_plugin_repo(plugin_home, tmp_path):
    """``-e`` accepts an addons-style repo: bare dir of plugin subpackages."""
    repo = tmp_path / "osh-contrib"
    sub = repo / "osh_example"
    sub.mkdir(parents=True)
    (sub / "__init__.py").write_text("OSH_PLUGIN_MANIFEST = {}\n")

    runner = CliRunner()
    result = runner.invoke(plug, ["install", "--trust", "-e", str(repo)])

    assert result.exit_code == 0, result.output
    link = plugin_home / "osh-contrib"
    assert link.is_symlink()
    assert link.resolve() == repo.resolve()


def test_install_clones_git_url(plugin_home, monkeypatch):
    """A plain install still runs ``git clone`` into the plugin dir."""
    calls = []
    monkeypatch.setattr(
        "osh.commands.plug_cmd.run_subprocess",
        lambda args, **kw: calls.append(args) or (0, "", ""),
    )
    runner = CliRunner()
    result = runner.invoke(
        plug, ["install", "--trust", "https://github.com/acme/osh-foo.git"]
    )

    assert result.exit_code == 0, result.output
    assert calls[0][:3] == ["git", "clone", "--depth"]
    assert calls[0][-1].endswith("osh-foo")


def test_install_rejects_non_url(plugin_home):
    """Without ``-e`` the source must look like a git URL."""
    runner = CliRunner()
    result = runner.invoke(plug, ["install", "--trust", "/some/local/dir"])

    assert result.exit_code != 0
    assert "must be a git repository" in result.output


def test_install_refuses_existing_plugin(plugin_home, src_plugin):
    """Installing over an existing plugin name fails."""
    (plugin_home / "my_plugin").mkdir(parents=True)
    runner = CliRunner()
    result = runner.invoke(plug, ["install", "--trust", "-e", str(src_plugin)])

    assert result.exit_code != 0
    assert "already installed" in result.output


def test_uninstall_removes_symlink_keeps_source(plugin_home, src_plugin):
    """Uninstalling an editable plugin removes the link, not the files."""
    link = plugin_home / "my_plugin"
    plugin_home.mkdir(parents=True)
    link.symlink_to(src_plugin.resolve())

    runner = CliRunner()
    result = runner.invoke(plug, ["uninstall", "--yes", "my_plugin"])

    assert result.exit_code == 0, result.output
    assert not link.exists() and not link.is_symlink()
    assert (src_plugin / "__init__.py").is_file()


def test_uninstall_removes_cloned_plugin(plugin_home):
    """Uninstalling a regular plugin removes its directory tree."""
    plugin = plugin_home / "my_plugin"
    plugin.mkdir(parents=True)
    (plugin / "__init__.py").write_text("")

    runner = CliRunner()
    result = runner.invoke(plug, ["uninstall", "--yes", "my_plugin"])

    assert result.exit_code == 0, result.output
    assert not plugin.exists()


def test_uninstall_missing_plugin(plugin_home):
    """Uninstalling an unknown plugin name fails."""
    runner = CliRunner()
    result = runner.invoke(plug, ["uninstall", "--yes", "ghost"])

    assert result.exit_code != 0
    assert "not installed" in result.output


def test_list_marks_editable_plugins(plugin_home, src_plugin):
    """``osh plug list`` marks symlinked plugins as editable."""
    plugin_home.mkdir(parents=True)
    (plugin_home / "my_plugin").symlink_to(src_plugin.resolve())
    cloned = plugin_home / "cloned_plugin"
    cloned.mkdir()

    runner = CliRunner()
    result = runner.invoke(plug, ["list"])

    assert result.exit_code == 0, result.output
    assert "my_plugin (editable)" in result.output
    assert "cloned_plugin" in result.output
    assert "cloned_plugin (editable)" not in result.output
