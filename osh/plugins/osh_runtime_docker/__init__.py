"""Built-in Docker runtime plugin for Osh.

Provides the ``docker`` runtime for ``osh odoo``/``osh shell`` and the
``osh docker`` command group (``init``, ``activate``, ``list``,
``stop``), reading an existing Docker Compose stack configuration from
``.osh/docker.toml``.
"""

from .commands import Docker  # noqa: F401 — re-exported for command discovery
from .runtimes import DockerRuntime  # noqa: F401 — re-exported for runtime discovery
