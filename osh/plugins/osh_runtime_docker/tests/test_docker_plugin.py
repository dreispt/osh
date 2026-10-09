"""Tests for the built-in Docker runtime plugin registration."""

import sys
import types

import click

from osh.runtimes import Runtime
from osh.utils.plugin_loader import load_plugins, load_runtimes


def test_docker_runtime_is_registered():
    """The docker plugin registers the unified Docker runtime."""
    runtimes = load_runtimes()
    assert "docker" in runtimes
    assert runtimes["docker"].name == "docker"
    assert runtimes["docker"].runtime_type == "runtime"


def test_load_runtimes_warns_on_name_collision(monkeypatch, capsys):
    """A runtime name collision is reported instead of silently ignored."""
    from osh.utils import plugin_loader, plugin_registry

    class FakeRuntime(Runtime):
        name = "docker"

    class OtherRuntime(Runtime):
        name = "docker"

    first = types.ModuleType("first")
    first.FakeRuntime = FakeRuntime
    second = types.ModuleType("second")
    second.OtherRuntime = OtherRuntime

    # Isolate the registry so only the patched modules contribute runtimes.
    monkeypatch.setattr(plugin_registry, "_REGISTRY", plugin_registry.PluginRegistry())
    monkeypatch.setattr(
        plugin_loader,
        "_iter_plugin_modules",
        lambda: [("first", first), ("second", second)],
    )

    runtimes = load_runtimes()
    assert runtimes["docker"] is FakeRuntime
    err = capsys.readouterr().err
    assert "runtime 'docker' from 'second' conflicts" in err


def test_entry_point_plugin_loading(monkeypatch):
    """``module:attr`` entry points resolve their command lazily."""
    from osh.utils import plugin_registry

    fake_cmd = click.Command(name="fake-cmd")

    fake_module = types.ModuleType("fake_entry_plugin")
    fake_module.fake_cmd = fake_cmd
    monkeypatch.setitem(sys.modules, "fake_entry_plugin", fake_module)

    class FakeEntryPoint:
        def __init__(self, name, value, group="osh.plugins"):
            self.name = name
            self.value = value
            self.group = group

    class FakeEntryPoints:
        def __init__(self, eps):
            self._eps = eps

        def select(self, **kwargs):
            if kwargs.get("group") == "osh.plugins":
                return self._eps
            return []

    fake_metadata = types.ModuleType("fake_metadata")
    fake_metadata.entry_points = lambda: FakeEntryPoints(
        [FakeEntryPoint("fake", "fake_entry_plugin:fake_cmd")]
    )
    monkeypatch.setattr(plugin_registry, "_metadata", fake_metadata)

    commands = {cmd.name: (src, cmd) for src, cmd in load_plugins()}
    src, cmd = commands["fake"]
    assert src == "fake"
    assert cmd.load() is fake_cmd
