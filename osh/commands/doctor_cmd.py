"""`osh doctor` command implementation."""

import click

from ..commons import find_project_root
from ..db import get_project_config
from ..diagnostics import collect_diagnostics, report_diagnostics
from ..plugin_loader import load_backends
from ..verbosity import get_verbosity


@click.command(name="doctor")
@click.option(
    "--verbose",
    is_flag=True,
    help="Show extra diagnostic details.",
)
@click.pass_context
def doctor(ctx, verbose):  # noqa: D401
    """Show project diagnostics by delegating to the active backend."""
    base = find_project_root(required=True)

    # Set up verbosity
    echo = get_verbosity(ctx, base, verbose_override=verbose)

    # Show friendly header for new users
    echo.guidance("Checking your Osh setup...")

    backend_name = get_project_config(base, "init", "target") or get_project_config(
        base, "run", "target"
    )

    backends = load_backends()
    if backend_name is None:
        echo.essential(
            "No installed targets. "
            "Run 'osh init --target <local|docker> <version>' first."
        )
        return

    echo.essential(f"Active target: {backend_name}")

    backend_cls = backends.get(backend_name)
    if backend_cls is None:
        raise click.ClickException(
            f"Unknown backend '{backend_name}'. "
            f"Available: {', '.join(backends)} or run 'osh init --target <backend>'."
        )

    backend = backend_cls()
    diagnostics = collect_diagnostics(base, backend, ctx, target=backend_name)
    report_diagnostics(diagnostics, echo)

    # Show friendly footer for new users
    if diagnostics.ready:
        echo.next_steps("Your setup looks good! Run 'osh run' to start Odoo.")
