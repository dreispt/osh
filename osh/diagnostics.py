"""Diagnostic collection and reporting for Osh commands.

Backends implement ``diagnose`` to inspect the environment and the current
project. The same diagnostics are reused by ``osh doctor`` (to report),
``osh init`` (to plan and ask for confirmation), and ``osh run`` (to check
prerequisites before executing).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

if TYPE_CHECKING:
    from .backends import Backend


@dataclass
class Diagnostics:
    """Container for environment checks, plans and final command data."""

    backend: str
    ready: bool = True
    project: Path | None = None
    target: str | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: dict[str, Any] = field(default_factory=dict)
    plan: list[str] = field(default_factory=list)
    command: list[str] | None = None

    def add_error(self, message: str) -> None:
        """Record a blocking error and mark the project as not ready."""
        self.errors.append(message)
        self.ready = False

    def add_warning(self, message: str) -> None:
        """Record a non-fatal warning."""
        self.warnings.append(message)

    def add_info(self, key: str, value: Any) -> None:
        """Record a piece of information for verbose reporting."""
        self.info[key] = value

    def add_plan(self, item: str) -> None:
        """Record a planned action, used by ``osh init``."""
        self.plan.append(item)


def collect_diagnostics(
    base: Path,
    backend: Backend,
    ctx: click.Context | None = None,
    *,
    target: str | None = None,
) -> Diagnostics:
    """Collect core and backend-specific diagnostics for *base*."""
    from .db import get_current_branch

    diagnostics = backend.diagnose(base, ctx)
    diagnostics.project = base
    diagnostics.target = target or backend.name
    branch = get_current_branch(base) or "default"
    diagnostics.add_info("git_branch", branch)
    return diagnostics


def report_diagnostics(diagnostics: Diagnostics, echo: Any) -> None:
    """Print *diagnostics* using the current verbosity object."""
    echo.essential(f"Backend: {diagnostics.backend}")
    if diagnostics.project:
        echo.essential(f"Project: {diagnostics.project}")
    echo.essential(f"Ready: {'yes' if diagnostics.ready else 'no'}")

    for error in diagnostics.errors:
        echo.error(error)
    for warning in diagnostics.warnings:
        echo.warning(warning)

    if diagnostics.info:
        for key, value in sorted(diagnostics.info.items()):
            echo.details(f"  {key}: {value}")

    if diagnostics.plan:
        echo.essential("Planned actions:")
        for item in diagnostics.plan:
            echo.essential(f"  - {item}")

    if diagnostics.command:
        echo.details(f"command: {' '.join(diagnostics.command)}")
