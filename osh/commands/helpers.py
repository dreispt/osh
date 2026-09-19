"""Diagnostic collection and reporting for Osh commands.

Backends implement ``diagnose`` to inspect the environment and the current
project. The same diagnostics are reused by ``osh init`` (to plan and ask
for confirmation) and ``osh odoo``/``osh shell`` (to check prerequisites
before executing).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import click

from .. import echo


@dataclass
class Diagnostics:
    """Container for environment checks, plans and final command data."""

    backend: str
    ready: bool = True
    project: Path | None = None
    target: str | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: dict[str, dict[str, Any]] = field(default_factory=dict)
    plan: list[str] = field(default_factory=list)
    command: list[str] | None = None

    def _default_topic(self):
        """Return the default topic for info entries."""
        return self.backend or "general"

    def add_error(self, message):
        """Record a blocking error and mark the project as not ready."""
        self.errors.append(message)
        self.ready = False

    def add_warning(self, message):
        """Record a non-fatal warning."""
        self.warnings.append(message)

    def add_info(self, key, value, *, topic=None):
        """Record a piece of information under a topic."""
        topic = topic or self._default_topic()
        self.info.setdefault(topic, {})[key] = value

    def add_plan(self, item):
        """Record a planned action, used by ``osh init``."""
        self.plan.append(item)


def collect_diagnostics(
    base,
    backend,
    ctx=None,
    *,
    target=None,
    sections=None,
    **options,
):
    """Collect backend-specific diagnostics for *base*."""
    diagnostics = backend.diagnose(base, ctx, sections=sections, **options)
    diagnostics.project = base
    diagnostics.target = target or backend.name
    return diagnostics


def check_run_diagnostics(base, backend, ctx, *, compose_file=None):
    """Collect run-phase diagnostics; print warnings, raise on errors.

    Shared pre-flight for ``osh odoo``/``osh shell`` and plugin commands that
    need the backend checked before executing (e.g. ``osh db restore``).
    Returns the collected ``Diagnostics``.
    """
    diagnostics = collect_diagnostics(
        base,
        backend,
        ctx,
        target=backend.name,
        phase="run",
        compose_file=compose_file,
        sections=backend.diagnose_sections_for_phase("run"),
    )
    for warning_msg in diagnostics.warnings:
        echo.warning(warning_msg)
    if diagnostics.errors:
        raise click.ClickException("\n".join(diagnostics.errors))
    return diagnostics
