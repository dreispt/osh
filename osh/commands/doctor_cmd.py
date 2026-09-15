"""`osh doctor` command implementation."""

import click

from .. import echo
from ..backends import NoneBackend
from ..common import find_project_root
from ..db import get_project_config, normalize_backend_name
from .helpers import collect_diagnostics, report_diagnostics


@click.command(name="doctor")
@click.pass_context
def doctor(ctx):  # noqa: D401
    """Show base project diagnostics.

    Backend-specific checks are reported by ``osh <backend> doctor``
    (e.g. ``osh docker doctor``, ``osh venv doctor``).
    """
    base = find_project_root(required=True)

    # Show friendly header for new users
    echo.friendly("Checking your Osh setup...")

    active_target = normalize_backend_name(
        get_project_config(base, "run", "target")
        or get_project_config(base, "init", "target")
    )

    diagnostics = collect_diagnostics(
        base,
        NoneBackend(),
        ctx,
        target=active_target or "none",
        check_nesting=True,
    )
    report_diagnostics(diagnostics)

    if active_target and active_target != "none":
        echo.info(f"Run 'osh {active_target} doctor' for backend-specific checks.")

    # Show friendly footer for new users
    if diagnostics.ready:
        echo.friendly("Your setup looks good! Run 'osh odoo' to start Odoo.")
