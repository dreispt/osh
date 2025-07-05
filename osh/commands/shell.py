"""`osh shell` command implementation."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Optional

import click

# Local imports
from ..utils import _parse_dsn, _run_file, _simple_repl

# Lazy IPython import for nicer shell, fallback to plain
try:
    import IPython  # type: ignore
except ImportError:  # pragma: no cover
    IPython = None  # type: ignore


@click.command(name="shell")
@click.option(
    "--dsn",
    envvar="ODOO_URL",
    default="http://localhost:8069",
    help="Server URL, e.g. http://user:pass@localhost:8069/dbname",
    show_default=True,
)
@click.option("--code", "-c", help="Python code to execute on the Odoo env and exit.")
@click.option("--file", "-f", type=click.Path(exists=True), help="Python file to run before dropping to shell.")
@click.pass_context
def shell(ctx: click.Context, dsn: str, code: Optional[str], file: Optional[str]) -> None:  # noqa: D401
    """Drop into an interactive Python shell connected to Odoo."""

    # Deferred import so users who only need the info command don't need odoorpc.
    try:
        import odoorpc  # type: ignore
    except ImportError as e:  # pragma: no cover
        click.echo("The \"odoorpc\" package is required for this command.", err=True)
        raise click.ClickException(str(e))

    url, dbname, username, password = _parse_dsn(dsn)

    click.echo(f"Connecting to {url}…", err=True)
    od = odoorpc.ODOO(url.hostname, port=url.port or 8069)

    if username and password:
        od.login(dbname, username, password)

    env: dict[str, Any] = {"odoo": od, "rpc": od, "env": od.env if hasattr(od, "env") else None}

    if file:
        _run_file(Path(file), env)
    if code:
        exec(code, env)  # nosec B102: deliberate
        return

    if IPython:
        IPython.start_ipython(argv=[], user_ns=env)
    else:
        _simple_repl(env)
