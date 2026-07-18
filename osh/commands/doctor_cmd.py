"""`osh doctor` command implementation."""

from __future__ import annotations

import click

from ..commons import find_project_root
from ..db import load_osh_config
from ..plugin_loader import load_backends
from ..verbosity import get_verbosity


@click.command(name="doctor")
@click.option(
    "--verbose",
    is_flag=True,
    help="Show extra diagnostic details.",
)
@click.pass_context
def doctor(ctx: click.Context, verbose: bool) -> None:  # noqa: D401
    """Show project diagnostics by delegating to the active backend."""
    base = find_project_root(required=True)

    # Set up verbosity
    echo = get_verbosity(ctx, base)

    # Show friendly header for new users
    echo.guidance("Checking your Osh setup...")

    cfg = load_osh_config(base)
    backend_name = cfg.get("run", "target", fallback="local")

    backends = load_backends()
    backend_cls = backends.get(backend_name)
    if backend_cls is None:
        raise click.ClickException(
            f"Unknown backend '{backend_name}'. "
            f"Available: {', '.join(backends)} or run 'osh init --target <backend>'."
        )

    for line in backend_cls().status(ctx, base, verbose=verbose):
        click.echo(line)

    # Show friendly footer for new users
    echo.next_steps("Your setup looks good! Run 'osh run' to start Odoo.")
