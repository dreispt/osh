"""Built-in virtualenv backend plugin for Osh."""

from .backends import VenvBackend  # noqa: F401 — re-exported for backend discovery
from .commands import Venv  # noqa: F401 — re-exported for handler discovery
