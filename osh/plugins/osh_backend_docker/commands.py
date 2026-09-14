"""Commands bundled with the ``docker`` backend plugin."""

from ...commands.backend_cmd import backend_group
from .backends import DockerBackend

docker = backend_group(DockerBackend)
