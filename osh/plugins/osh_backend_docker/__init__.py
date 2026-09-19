"""Built-in Docker backend plugin for Osh.

Provides the ``docker`` backend for ``osh odoo``/``osh shell`` and the
``osh docker`` command group (``init``, ``activate``, ``list``,
``stop``), reading an existing Docker Compose stack configuration from
``.osh/docker.toml``.
"""

from .backends import DockerBackend  # noqa: F401 — re-exported for backend discovery
from .commands import (  # noqa: F401 — re-exported for command discovery
    DockerActivate,
    DockerInit,
    DockerList,
    DockerStop,
)
