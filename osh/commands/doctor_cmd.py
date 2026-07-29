"""`osh doctor` command implementation."""

import click

from .. import echo
from ..common import find_project_root
from ..db import get_project_config
from ..utils.plugin_loader import load_backends
from .helpers import collect_diagnostics, report_diagnostics


@click.command(name="doctor")
@click.pass_context
def doctor(ctx):  # noqa: D401
    """Show project diagnostics by delegating to the active backend."""
    base = find_project_root(required=True)

    # Show friendly header for new users
    echo.friendly("Checking your Osh setup...")

    active_target = get_project_config(base, "init", "target") or get_project_config(
        base, "run", "target"
    )

    backends = load_backends()

    if active_target and active_target not in backends:
        raise click.ClickException(
            f"Unknown backend '{active_target}'. "
            f"Available: {', '.join(backends)} or run 'osh init --target <backend>'."
        )

    if not backends:
        echo.info("No backends are registered.")
        return

    all_diagnostics = []
    ordered = sorted(backends)
    if active_target and active_target in ordered:
        ordered.remove(active_target)
        ordered.insert(0, active_target)

    for backend_name in ordered:
        backend = backends[backend_name]()
        diagnostics = collect_diagnostics(
            base,
            backend,
            ctx,
            target=active_target if backend_name == active_target else backend_name,
            include_core=(backend_name == active_target),
        )
        all_diagnostics.append(diagnostics)
        report_diagnostics(diagnostics)

    # Show friendly footer for new users
    all_ready = all(d.ready for d in all_diagnostics)
    if all_ready:
        echo.friendly("Your setup looks good! Run 'osh odoo' to start Odoo.")
