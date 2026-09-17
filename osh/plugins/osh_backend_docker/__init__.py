"""Built-in Docker backend plugin for Osh.

Provides the ``docker`` backend for ``osh odoo``/``osh shell`` and the
``osh docker`` command group (``init``, ``activate``, ``doctor``, ``list``,
``stop``), reading an
existing Docker Compose stack configuration from ``.osh/docker.toml``.
"""

from .backends import DockerBackend
from .commands import docker

OSH_PLUGIN_MANIFEST = {"backends": [DockerBackend], "backend_commands": [docker]}
