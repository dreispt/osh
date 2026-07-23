"""`osh doctor` command implementation."""

import click

from .. import echo
from ..commons import find_project_root
from ..db import get_project_config
from ..diagnostics import collect_diagnostics, report_diagnostics
from ..plugin_loader import load_backends


@click.command(name="doctor")
@click.pass_context
def doctor(ctx):  # noqa: D401
    """Show project diagnostics by delegating to the active backend."""
    base = find_project_root(required=True)

    # Show friendly header for new users
    echo.friendly("Checking your Osh setup...")

    backend_name = get_project_config(base, "init", "target") or get_project_config(
        base, "run", "target"
    )

    backends = load_backends()

    if backend_name is None:
        echo.info(
            "No installed targets. "
            "Run 'osh init --target <local|docker> <version>' first."
        )
        return

    backend_cls = backends.get(backend_name)
    if backend_cls is None:
        raise click.ClickException(
            f"Unknown backend '{backend_name}'. "
            f"Available: {', '.join(backends)} or run 'osh init --target <backend>'."
        )

    backend = backend_cls()
    diagnostics = collect_diagnostics(base, backend, ctx, target=backend_name)
    report_diagnostics(diagnostics)

    # Show friendly footer for new users
    if diagnostics.ready:
        echo.friendly("Your setup looks good! Run 'osh run' to start Odoo.")
