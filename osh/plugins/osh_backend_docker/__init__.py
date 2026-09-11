"""Built-in Docker backend plugin for Osh.

Provides ``osh init --target docker`` and ``osh odoo --target docker`` support
by reading an existing Docker Compose stack configuration from ``.osh/docker.toml``.
"""

from .backends import DockerBackend

OSH_PLUGIN_MANIFEST = {"backends": [DockerBackend]}
