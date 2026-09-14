"""`osh down` command implementation.

Stops a rogue Odoo process left listening on the project's HTTP port.
This is a base operation — it only ever acts on host processes. Backends
with their own resources expose ``osh <backend> down`` instead (e.g.
``osh docker down`` runs ``docker compose down``).
"""

import click

from ..backends import NoneBackend
from ..common import find_project_root


@click.command(name="down")
@click.pass_context
def down(ctx):  # noqa: D401
    """Stop an Odoo process left running on the project's HTTP port."""
    base = find_project_root(required=True)
    NoneBackend().down(ctx, base)
