"""Built-in Doodba runtime plugin for Osh.

Provides the ``doodba`` runtime for ``osh init doodba``,
``osh odoo``/``osh exec`` and ``osh stop`` inside a Doodba project
(https://github.com/Tecnativa/doodba-copier-template).
"""

from .runtimes import DoodbaRuntime  # noqa: F401 — re-exported for runtime discovery
