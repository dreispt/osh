"""Diagnostic collection and reporting for Osh commands.

Backends implement ``diagnose`` to inspect the environment and the current
project. The same diagnostics are reused by ``osh doctor`` (to report),
``osh init`` (to plan and ask for confirmation), and ``osh run`` (to check
prerequisites before executing).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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

    def add_error(self, message):
        """Record a blocking error and mark the project as not ready."""
        self.errors.append(message)
        self.ready = False

    def add_warning(self, message):
        """Record a non-fatal warning."""
        self.warnings.append(message)

    def add_info(self, key, value):
        """Record a piece of information for verbose reporting."""
        self.info[key] = value

    def add_plan(self, item):
        """Record a planned action, used by ``osh init``."""
        self.plan.append(item)

    def report(
        self,
        echo,
        *,
        include_header=True,
        include_info=True,
        include_plans=False,
    ):
        """Print this diagnostics object using the current verbosity object."""
        if include_header:
            echo.essential(f"Backend: {self.backend}")
            if self.project:
                echo.essential(f"Project: {self.project}")
            echo.essential(f"Ready: {'yes' if self.ready else 'no'}")

        for error in self.errors:
            echo.error(error)
        for warning in self.warnings:
            echo.warning(warning)

        if include_plans and self.plan:
            echo.essential("Planned actions:")
            for item in self.plan:
                echo.essential(f"  - {item}")

        if include_info and self.info:
            for key, value in sorted(self.info.items()):
                echo.details(f"  {key}: {value}")


def collect_diagnostics(base, backend, ctx=None, *, target=None, sections=None):
    """Collect core and backend-specific diagnostics for *base*."""
    from .db import get_current_branch

    diagnostics = backend.diagnose(base, ctx, sections=sections)
    diagnostics.project = base
    diagnostics.target = target or backend.name
    branch = get_current_branch(base) or "default"
    diagnostics.add_info("git_branch", branch)
    return diagnostics


def report_diagnostics(diagnostics, echo):
    """Print *diagnostics* using the current verbosity object."""
    diagnostics.report(echo, include_plans=False, include_info=True)
