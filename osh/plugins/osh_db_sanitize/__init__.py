"""Built-in `osh db sanitize` plugin for Osh.

Provides the `osh db sanitize` group subcommand: an anonymize step that
rewrites PII with deterministic placeholders (bundled
``osh/data/anonymize/`` profile plus layered user/project scripts), and
a trim step that deletes low-value rows from big tables (a bundled
watchlist plus ``[trim]`` project config and ``trim`` scripts).
"""

from .sanitize_cmd import DbSanitize  # noqa: F401 — re-exported for discovery
