"""`osh doctor` command implementation."""

from __future__ import annotations

import click

from ..db import _load_osh_config
from ..plugin_loader import load_backends
from ..utils import _find_project_root


@click.command(name="doctor")
@click.pass_context
def doctor(ctx: click.Context) -> None:  # noqa: D401
    """Show project diagnostics by delegating to the active backend."""
    base = _find_project_root(required=True)

    cfg = _load_osh_config(base)
    backend_name = cfg.get("run", "target", fallback="local")

    backends = load_backends()
    backend_cls = backends.get(backend_name)
    if backend_cls is None:
        raise click.ClickException(
            f"Unknown backend '{backend_name}'. "
            f"Available: {', '.join(backends)} or run 'osh init --target <backend>'."
        )

    for line in backend_cls().status(ctx, base):
        click.echo(line)
