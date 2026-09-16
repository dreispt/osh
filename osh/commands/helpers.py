"""Diagnostic collection and reporting for Osh commands.

Backends implement ``diagnose`` to inspect the environment and the current
project. The same diagnostics are reused by ``osh doctor`` (to report),
``osh init`` (to plan and ask for confirmation), and ``osh odoo`` (to check
prerequisites before executing).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import click

from .. import echo
from ..common import find_enclosing_project, find_nested_projects
from ..config import get_init_parent
from ..db import get_current_branch, resolve_db_name


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

    def report(
        self,
        *,
        include_header=True,
        include_info=True,
    ):
        """Print this diagnostics object using the cached echo functions."""
        if include_header:
            echo.info(f"Ready: {'yes' if self.ready else 'no'}")

        for error_msg in self.errors:
            echo.error(error_msg)
        for warning_msg in self.warnings:
            echo.warning(warning_msg)

        if include_info and self.info:
            topics = [t for t in ("Project", "System") if t in self.info]
            topics += sorted(t for t in self.info if t not in topics)
            for topic in topics:
                echo.info(f"{topic}:")
                for key in sorted(self.info[topic]):
                    label = "Odoo version" if key == "odoo_version" else key
                    echo.info(f"  {label}: {self.info[topic][key]}")


def collect_diagnostics(
    base,
    backend,
    ctx=None,
    *,
    target=None,
    sections=None,
    include_core=True,
    check_nesting=False,
    **options,
):
    """Collect core and backend-specific diagnostics for *base*.

    *check_nesting* adds project-layout warnings about enclosing and
    nested Osh projects. It is opt-in because warnings collected here are
    also printed by ``check_run_diagnostics`` on every ``osh odoo``/``osh
    shell`` run — doctors enable it, run commands do not.
    """
    diagnostics = backend.diagnose(base, ctx, sections=sections, **options)
    diagnostics.project = base
    diagnostics.target = target or backend.name
    if include_core:
        branch = get_current_branch(base) or "default"
        diagnostics.add_info("project", str(base), topic="Project")
        diagnostics.add_info("git_branch", branch, topic="Project")
        diagnostics.add_info("active_target", diagnostics.target, topic="Project")
        diagnostics.add_info(
            "dbname", resolve_db_name(base, verbose=False), topic="Project"
        )
    if check_nesting:
        _add_nesting_diagnostics(diagnostics, base)
    return diagnostics


def _add_nesting_diagnostics(diagnostics, base):
    """Report enclosing and nested Osh projects around *base*.

    An enclosing project acknowledged through ``init.parent`` (recorded by
    ``osh init`` when the nesting was confirmed) is informational; an
    unacknowledged one is a warning, as it usually means an accidental
    nested ``.osh`` shadowing the intended environment.
    """
    enclosing = find_enclosing_project(base)
    if enclosing is not None:
        if get_init_parent(base) == enclosing:
            diagnostics.add_info("parent_project", str(enclosing), topic="Project")
        else:
            diagnostics.add_warning(
                f"This project is nested inside the Osh project at "
                f"'{enclosing}'. Commands run here use this environment; "
                f"delete '{base / '.osh'}' to use the parent project."
            )
    nested = find_nested_projects(base)
    for path in nested:
        diagnostics.add_warning(
            f"Nested Osh project at '{path.relative_to(base)}' — commands "
            "run inside it use that environment instead of this one."
        )


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


def report_diagnostics(diagnostics):
    """Print *diagnostics* using the cached echo functions."""
    diagnostics.report(include_info=True)
