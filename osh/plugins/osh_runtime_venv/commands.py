"""Commands bundled with the ``venv`` runtime plugin."""

from ...commands.runtime_cmd import RuntimeCommands


class Venv(RuntimeCommands):
    """The ``osh venv`` command group — virtualenv runtime lifecycle.

    ``init``, ``activate`` and ``stop`` all come from ``RuntimeCommands``.
    """

    _cli_name = "venv"
