"""``osh addon`` command group — Odoo module lifecycle commands.

Core ships the group as the stable attachment point for module commands;
plugins contribute the actual lifecycle actions (``osh addon update``,
``osh addon uninstall``, ...) through the ``group_commands`` metadata
section.
"""

import click

from ..cli_utils import NaturalOrderGroup


@click.group(name="addon", cls=NaturalOrderGroup)
def addon():
    """Manage Odoo modules — commands are provided by plugins."""
