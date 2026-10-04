"""Diagnostic collection and reporting for Osh commands.

Runtimes implement ``diagnose`` to inspect the environment and the current
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


@dataclass(init=False)
class Diagnostics:
    """Container for environment checks, plans and final command data."""

    runtime: str
    ready: bool = True
    project: Path | None = None
    target: str | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: dict[str, dict[str, Any]] = field(default_factory=dict)
    plan: list[str] = field(default_factory=list)
    command: list[str] | None = None

    def __init__(
        self,
        runtime="",
        ready=True,
        project=None,
        target=None,
        errors=None,
        warnings=None,
        info=None,
        plan=None,
        command=None,
        backend=None,
    ):
        # ``backend`` is the deprecated name of the ``runtime`` argument.
        self.runtime = runtime or backend or ""
        self.ready = ready
        self.project = project
        self.target = target
        self.errors = errors if errors is not None else []
        self.warnings = warnings if warnings is not None else []
        self.info = info if info is not None else {}
        self.plan = plan if plan is not None else []
        self.command = command

    @property
    def backend(self):
        """Deprecated alias for :attr:`runtime`."""
        return self.runtime

    @backend.setter
    def backend(self, value):
        self.runtime = value

    def _default_topic(self):
        """Return the default topic for info entries."""
        return self.runtime or "general"

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
    runtime,
    ctx=None,
    *,
    target=None,
    sections=None,
    **options,
):
    """Collect runtime-specific diagnostics for *base*."""
    diagnostics = runtime.diagnose(base, ctx, sections=sections, **options)
    diagnostics.project = base
    diagnostics.target = target or runtime.name
    return diagnostics


def check_run_diagnostics(base, runtime, ctx, *, compose_file=None):
    """Collect run-phase diagnostics; print warnings, raise on errors.

    Shared pre-flight for ``osh odoo``/``osh shell`` and plugin commands that
    need the runtime checked before executing (e.g. ``osh backup restore``).
    Returns the collected ``Diagnostics``.
    """
    diagnostics = collect_diagnostics(
        base,
        runtime,
        ctx,
        target=runtime.name,
        phase="run",
        compose_file=compose_file,
        sections=runtime.diagnose_sections_for_phase("run"),
    )
    for warning_msg in diagnostics.warnings:
        echo.warning(warning_msg)
    if diagnostics.errors:
        raise click.ClickException("\n".join(diagnostics.errors))
    return diagnostics
