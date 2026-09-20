"""``osh addon`` command group — Odoo module lifecycle commands.

Core ships the group as the stable attachment point for module commands;
plugins contribute the actual lifecycle actions (``osh addon update``,
``osh addon uninstall``, ...) through the ``group_commands`` metadata
section — or by subclassing ``Addon`` and adding ``@subcommand``
methods.
"""

from ..cli_utils import handler_group
from ..handlers import CommandHandler


class Addon(CommandHandler):
    """Manage Odoo modules — commands are provided by plugins."""

    _cli_name = "addon"


addon = handler_group("addon", Addon)
