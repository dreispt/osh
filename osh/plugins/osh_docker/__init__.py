"""Built-in Docker backend plugin for Osh.

Provides ``osh init-docker`` and ``osh run --target docker`` support by reading
an existing Docker Compose stack configuration from ``.osh/docker.toml``.
"""

from __future__ import annotations

from .backends import DockerBackend
from .commands import init_docker


def get_backends() -> list[type]:
    """Return backend classes exposed by this plugin."""
    return [DockerBackend]


def get_commands() -> list:
    """Return Click commands exposed by this plugin."""
    return [init_docker]
