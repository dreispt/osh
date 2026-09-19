"""Tests for ``osh plug`` install/list/uninstall."""

import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from osh.commands.plug_cmd import plug

PLUGINS_DATA = Path(__file__).parent / "plugins"


@pytest.fixture
def plugin_home(tmp_path, monkeypatch):
    """Redirect the user plugin directory to a temporary location."""
    target = tmp_path / "plugins"
    monkeypatch.setattr("osh.commands.plug_cmd.user_plugin_dir", lambda: target)
    return target


@pytest.fixture
def src_plugin():
    """Return the static plugin package directory to install from."""
    return PLUGINS_DATA / "plug_src" / "my_plugin"


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
    assert "does not contain any plugin" in result.output


def test_install_editable_accepts_bare_plugin_repo(plugin_home, tmp_path):
    """``-e`` accepts an addons-style repo: bare dir of plugin subpackages."""
    repo = PLUGINS_DATA / "plug_repo"

    runner = CliRunner()
    result = runner.invoke(plug, ["install", "--trust", "-e", str(repo)])

    assert result.exit_code == 0, result.output
    link = plugin_home / repo.name
    assert link.is_symlink()
    assert link.resolve() == repo.resolve()


def test_install_clones_git_url(plugin_home, monkeypatch):
    """A plain install still runs ``git clone`` into the plugin dir."""
    calls = []

    def fake_clone(args, **kw):
        calls.append(args)
        shutil.copytree(PLUGINS_DATA / "plug_cloned", Path(args[-1]))
        return 0, "", ""

    monkeypatch.setattr("osh.commands.plug_cmd.run_subprocess", fake_clone)
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
    plugin_home.mkdir(parents=True, exist_ok=True)
    shutil.copytree(PLUGINS_DATA / "plug_src" / "my_plugin", plugin)

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


@pytest.fixture
def user_config(tmp_path, monkeypatch):
    """Redirect the user config file to a temporary location."""
    config_file = tmp_path / "config.toml"
    monkeypatch.setattr("osh.config.get_user_config_path", lambda: config_file)
    return config_file


def _make_multi_repo(tmp_path):
    """Return the static multi-plugin repo directory."""
    return PLUGINS_DATA / "plug_multi" / "osh-contrib"


def test_install_multi_plugin_all(plugin_home, user_config, tmp_path):
    """``--all`` enables every plugin in a multi-plugin repo."""
    repo = _make_multi_repo(tmp_path)

    runner = CliRunner()
    result = runner.invoke(plug, ["install", "--trust", "-e", "--all", str(repo)])

    assert result.exit_code == 0, result.output
    from osh.config import get_enabled_plugins

    assert get_enabled_plugins("osh-contrib") is None


def test_install_multi_plugin_subset(plugin_home, user_config, tmp_path):
    """``--plugin`` enables only the selected plugins."""
    repo = _make_multi_repo(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        plug,
        ["install", "--trust", "-e", "--plugin", "plug-a", str(repo)],
    )

    assert result.exit_code == 0, result.output
    from osh.config import get_enabled_plugins

    assert get_enabled_plugins("osh-contrib") == ["plug-a"]


def test_install_multi_plugin_requires_selection(plugin_home, tmp_path):
    """A multi-plugin repo without selection flags fails with a hint."""
    repo = _make_multi_repo(tmp_path)

    runner = CliRunner()
    result = runner.invoke(plug, ["install", "--trust", "-e", str(repo)])

    assert result.exit_code != 0
    assert "--all" in result.output


def test_install_multi_plugin_unknown_name(plugin_home, tmp_path):
    """``--plugin`` with an unknown name fails listing available plugins."""
    repo = _make_multi_repo(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        plug, ["install", "--trust", "-e", "--plugin", "nope", str(repo)]
    )

    assert result.exit_code != 0
    assert "not found" in result.output
    assert "plug-a" in result.output


def _install_repo(plugin_home, tmp_path):
    """Symlink a multi-plugin repo into the plugin dir, all enabled."""
    repo = _make_multi_repo(tmp_path)
    plugin_home.mkdir(parents=True, exist_ok=True)
    (plugin_home / "osh-contrib").symlink_to(repo.resolve())
    return repo


def test_enable_disable_roundtrip(plugin_home, user_config, tmp_path):
    """``osh plug enable``/``disable`` update the repo's enabled list."""
    _install_repo(plugin_home, tmp_path)
    from osh.config import get_enabled_plugins

    runner = CliRunner()
    result = runner.invoke(plug, ["disable", "osh-contrib", "plug-b"])
    assert result.exit_code == 0, result.output
    assert get_enabled_plugins("osh-contrib") == ["plug-a"]

    result = runner.invoke(plug, ["enable", "osh-contrib", "plug-b"])
    assert result.exit_code == 0, result.output
    assert sorted(get_enabled_plugins("osh-contrib")) == ["plug-a", "plug-b"]

    result = runner.invoke(plug, ["disable", "osh-contrib"])
    assert result.exit_code == 0, result.output
    assert get_enabled_plugins("osh-contrib") == []

    result = runner.invoke(plug, ["enable", "osh-contrib"])
    assert result.exit_code == 0, result.output
    assert get_enabled_plugins("osh-contrib") is None


def test_enable_unknown_plugin_errors(plugin_home, user_config, tmp_path):
    """Enabling a plugin name that does not exist in the repo fails."""
    _install_repo(plugin_home, tmp_path)

    runner = CliRunner()
    result = runner.invoke(plug, ["enable", "osh-contrib", "nope"])

    assert result.exit_code != 0
    assert "not found" in result.output


def test_alias_and_unalias_persist(plugin_home, user_config, tmp_path):
    """``osh plug alias`` stores a permanent name; ``unalias`` removes it."""
    _install_repo(plugin_home, tmp_path)
    from osh.config import get_plugin_aliases

    runner = CliRunner()
    result = runner.invoke(plug, ["alias", "plug-a", "db.restore", "load-db"])
    assert result.exit_code == 0, result.output
    assert get_plugin_aliases("plug-a") == {"db.restore": "load-db"}

    result = runner.invoke(plug, ["unalias", "plug-a", "db.restore"])
    assert result.exit_code == 0, result.output
    assert get_plugin_aliases("plug-a") == {}


def test_unalias_without_alias_errors(plugin_home, user_config, tmp_path):
    """``unalias`` on a command with no alias fails cleanly."""
    _install_repo(plugin_home, tmp_path)

    runner = CliRunner()
    result = runner.invoke(plug, ["unalias", "plug-a", "scan"])

    assert result.exit_code != 0
    assert "no alias" in result.output


def test_alias_rejects_invalid_name(plugin_home, user_config, tmp_path):
    """An alias with illegal characters is rejected."""
    _install_repo(plugin_home, tmp_path)

    runner = CliRunner()
    result = runner.invoke(plug, ["alias", "plug-a", "scan", "not a name!"])

    assert result.exit_code != 0
    assert "Invalid command name" in result.output


def test_uninstall_clears_enabled_list(plugin_home, user_config, tmp_path):
    """``osh plug uninstall`` drops the repo's enabled list from config."""
    _install_repo(plugin_home, tmp_path)
    from osh.config import get_enabled_plugins, set_enabled_plugins

    set_enabled_plugins("osh-contrib", ["plug-a"])
    assert get_enabled_plugins("osh-contrib") == ["plug-a"]

    runner = CliRunner()
    result = runner.invoke(plug, ["uninstall", "--yes", "osh-contrib"])

    assert result.exit_code == 0, result.output
    assert get_enabled_plugins("osh-contrib") is None
