"""`osh venv` command implementation.

Enters the project's virtualenv or runs a command in it.
"""

import os
import shutil

import click

from ..common import find_project_root


def _get_venv_bin(base):
    """Return the virtualenv binary directory for *base*."""
    return base / ".venv" / ("Scripts" if os.name == "nt" else "bin")


def _activate_venv(base):
    """Put the project's .venv first on PATH and set VIRTUAL_ENV.

    Raises a ClickException if the virtualenv does not exist.
    """
    venv_bin = _get_venv_bin(base)
    if not venv_bin.is_dir():
        raise click.ClickException(
            "No virtualenv found. Run `osh init --target local` to create one."
        )
    venv_path = str(venv_bin)
    old_path = os.environ.get("PATH", "")
    if venv_path not in old_path.split(os.pathsep):
        os.environ["PATH"] = f"{venv_path}{os.pathsep}{old_path}"
    os.environ["VIRTUAL_ENV"] = str(venv_bin.parent)
    return venv_bin


def _find_shell():
    """Return a shell to launch for `osh venv` without arguments."""
    if os.name == "nt":
        return os.environ.get("COMSPEC", "cmd.exe")
    shell = os.environ.get("SHELL")
    if shell:
        return shell
    for fallback in ("bash", "sh", "zsh"):
        found = shutil.which(fallback)
        if found:
            return found
    raise click.ClickException("Could not determine a shell to launch.")


@click.command(
    name="venv",
    context_settings=dict(ignore_unknown_options=True),
)
@click.argument("extra_args", nargs=-1, type=click.UNPROCESSED)
def venv(extra_args):
    """Enter the project's virtualenv or run a command in it.

    Without arguments this opens an interactive shell with the virtualenv on
    PATH. Any arguments are passed through as a command to run inside the
    virtualenv.

    Examples:

    \b
      osh venv
      osh venv python --version
      osh venv pip list
      osh venv -- pytest tests/
    """
    base = find_project_root(required=True)
    _activate_venv(base)
    args = list(extra_args)
    if args and args[0] == "--":
        args.pop(0)
    if not args:
        shell = _find_shell()
        os.execvp(shell, [shell])
        return
    os.execvp(args[0], args)
