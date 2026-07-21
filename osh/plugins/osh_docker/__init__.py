"""Built-in Docker backend plugin for Osh.

Provides ``osh init --target docker`` and ``osh run --target docker`` support
by reading an existing Docker Compose stack configuration from ``.osh/docker.toml``.
"""

from .backends import DockerBackend


def get_backends():
    """Return backend classes exposed by this plugin."""
    return [DockerBackend]


def get_commands():
    """Return Click commands exposed by this plugin."""
    return []
