"""Built-in Docker runtime plugin for Osh.

Provides the ``docker`` runtime for ``osh init docker``,
``osh odoo``/``osh shell`` and ``osh stop``, reading an existing Docker
Compose stack configuration from ``.osh/docker.toml``.
"""

from .runtimes import DockerRuntime  # noqa: F401 — re-exported for runtime discovery
