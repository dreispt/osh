"""Commands bundled with the ``venv`` runtime plugin."""

from ...commands.backend_cmd import BackendCommands


class Venv(BackendCommands):
    """The ``osh venv`` command group — virtualenv runtime lifecycle.

    ``init``, ``activate`` and ``stop`` all come from ``BackendCommands``.
    """

    _cli_name = "venv"
