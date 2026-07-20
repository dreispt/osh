"""`osh python` and `osh pip` command implementations.

Thin wrappers around the project's virtualenv Python interpreter and its pip.
"""

import os

import click

from ..commons import find_project_root
from ..plugins.osh_local.utils import _get_venv_python


def _get_venv_python_for_project(base):
    """Return the Python interpreter inside the project's .venv, or raise."""
    venv_bin = base / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    python = _get_venv_python(venv_bin / "python")
    if python is None:
        raise click.ClickException(
            "No virtualenv found. Run `osh init --target local` to create one."
        )
    if not str(python).startswith(str(venv_bin)):
        raise click.ClickException(
            "Could not locate a Python interpreter inside the project's virtualenv."
        )
    return python


@click.command(
    name="python",
    context_settings=dict(ignore_unknown_options=True, help_option_names=[]),
)
@click.argument("extra_args", nargs=-1, type=click.UNPROCESSED)
def python(extra_args):
    """Run the project's virtualenv Python interpreter.

    Examples:

    \b
      osh python
      osh python -c "import odoo; print(odoo.__version__)"
    """
    base = find_project_root(required=True)
    python = _get_venv_python_for_project(base)
    args = [str(python), *list(extra_args)]
    try:
        os.execvp(args[0], args)
    except Exception as exc:  # pragma: no cover
        raise click.ClickException(str(exc))


@click.command(
    name="pip",
    context_settings=dict(ignore_unknown_options=True, help_option_names=[]),
)
@click.argument("extra_args", nargs=-1, type=click.UNPROCESSED)
def pip(extra_args):
    """Run pip inside the project's virtualenv.

    Examples:

    \b
      osh pip install --upgrade odoo
      osh pip list
    """
    base = find_project_root(required=True)
    python = _get_venv_python_for_project(base)
    args = [str(python), "-m", "pip", *list(extra_args)]
    try:
        os.execvp(args[0], args)
    except Exception as exc:  # pragma: no cover
        raise click.ClickException(str(exc))
