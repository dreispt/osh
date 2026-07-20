"""Common helpers shared across Osh core and plugins.

This module hosts backend-agnostic utilities used by multiple plugins and
core commands: project root discovery, path conventions, tool availability
checks, and addon discovery. Functions here are intentionally public (no
leading underscore) since they form the shared library contract between
core and plugins.
"""

import configparser
import shlex
import shutil
import subprocess
import threading
from pathlib import Path

import click

DEFAULT_ODOO_DATA_DIR = Path.home() / ".local" / "share" / "Odoo"


def _find_git_root(start):
    """Return the nearest ancestor (including *start*) that is a git repo root.

    A git repo root is a directory containing a ``.git`` entry (directory for
    normal repos, file for submodules and worktrees).
    """
    for p in [start] + list(start.parents):
        if (p / ".git").exists():
            return p
    return None


def find_project_root(start=None, *, required=False):
    """Return the project root containing a ``.osh`` directory.

    When inside a git repository, the ``.osh`` directory is expected at the
    git root. If the git root itself has no ``.osh``, the immediate parent is
    checked as well (to support running from inside a git submodule of the
    actual project). The search does **not** walk past the git boundary.

    When not inside a git repository, falls back to walking up from *start*
    looking for a ``.osh`` directory.

    When *required* is True, print an informational message and exit if no
    project is found, instead of returning None.
    """
    start = (start or Path.cwd()).resolve()

    git_root = _find_git_root(start)
    if git_root is not None:
        if (git_root / ".osh").exists():
            return git_root
        # Submodule case: .osh lives one level above the submodule's git root.
        parent = git_root.parent
        if parent != git_root and (parent / ".osh").exists():
            return parent
        if required:
            _not_in_project()
        return None

    # No git repo: walk up looking for .osh (supports non-git projects).
    for p in [start] + list(start.parents):
        if (p / ".osh").exists():
            return p
    if required:
        _not_in_project()
    return None


def _not_in_project():
    """Print a helpful message and exit when no Osh project is found."""
    click.echo(
        "Not inside an Osh project. "
        "Run 'osh init --target <local|docker> <version>' to create one."
    )
    raise SystemExit(0)


def get_odoo_config_path(base):
    """Return path to the Odoo configuration file (.odoorc) in the project root."""
    return base / ".odoorc"


def get_osh_config_path(base):
    """Return path to the Osh project configuration file (.osh/config)."""
    return base / ".osh" / "config"


def _has_arg(args, long, short=None):
    """Return True if *args* contains the given long (and optional short) option."""
    for arg in args:
        if arg == long or arg.startswith(f"{long}="):
            return True
        if short and (arg == short or arg.startswith(short)):
            return True
    return False


def resolve_config_file(base, extra_args, *, for_run=False):
    """Return the appropriate Odoo config file path to use.

    Args:
        base: Project root directory
        extra_args: Command line arguments to check for explicit config
        for_run: If True, create .osh/odoo.conf if it doesn't exist (for run command)

    Returns:
        Path to config file to use, or None if no config should be used
    """
    has_explicit_config = _has_arg(extra_args, "--config", short="-c")
    if has_explicit_config:
        return None

    osh_odoo_conf = base / ".osh" / "odoo.conf"
    odoo_rc = get_odoo_config_path(base)

    # Prefer .osh/odoo.conf, fall back to .odoorc
    config_to_use = None
    if osh_odoo_conf.exists():
        config_to_use = osh_odoo_conf
    elif odoo_rc.exists():
        config_to_use = odoo_rc

    # For run command, create .osh/odoo.conf if it doesn't exist
    if for_run and config_to_use is None:
        osh_odoo_conf.parent.mkdir(parents=True, exist_ok=True)
        osh_odoo_conf.touch()
        config_to_use = osh_odoo_conf

    return config_to_use


def ensure_tool(tool):
    """Raise a ClickException if *tool* is not available on PATH."""
    if not shutil.which(tool):
        raise click.ClickException(f"Required tool '{tool}' is not available on PATH.")


def _stream_output(pipe, err=False):
    """Read *pipe* line-by-line and echo it to the user."""
    for line in iter(pipe.readline, ""):
        if not line:
            break
        click.echo(line.rstrip("\r\n"), err=err)
    pipe.close()


def run_command(
    args,
    *,
    cwd=None,
    check=False,
    capture_output=True,
    text=True,
    stream=False,
):
    """Run *args* and return the completed process.

    When *stream* is True the command's stdout and stderr are echoed to the
    user as they are produced. This is useful for long-running commands such
    as Docker builds or git clones.

    When *check* is True, a non-zero exit code or a missing executable raises a
    ``click.ClickException`` whose message includes the attempted command and
    any captured output.
    """
    if stream:
        if not text:
            raise ValueError("stream=True requires text=True")
        try:
            proc = subprocess.Popen(
                args,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=text,
            )
        except FileNotFoundError as exc:
            cmd = " ".join(shlex.quote(str(a)) for a in args)
            raise click.ClickException(f"Command not found: {cmd}") from exc

        out_thread = threading.Thread(
            target=_stream_output, args=(proc.stdout, False), daemon=True
        )
        err_thread = threading.Thread(
            target=_stream_output, args=(proc.stderr, True), daemon=True
        )
        out_thread.start()
        err_thread.start()

        returncode = proc.wait()
        out_thread.join()
        err_thread.join()

        result = subprocess.CompletedProcess(args=args, returncode=returncode)
        if check and returncode:
            cmd = " ".join(shlex.quote(str(a)) for a in args)
            raise click.ClickException(f"Command failed (exit {returncode}): {cmd}")
        return result

    try:
        return subprocess.run(
            args,
            cwd=cwd,
            check=check,
            capture_output=capture_output,
            text=text,
        )
    except subprocess.CalledProcessError as exc:
        cmd = " ".join(exc.cmd)
        output = "\n".join(
            part for part in [exc.stdout or "", exc.stderr or ""] if part
        )
        message = f"Command failed: {cmd}"
        if output:
            message += f"\n{output}"
        raise click.ClickException(message) from exc
    except FileNotFoundError as exc:
        raise click.ClickException(f"Command not found: {' '.join(args)}") from exc


def discover_addons_paths(base, *, max_depth=3):
    """Return a list of addon directories under *base*.

    An *addon* is recognised if the directory contains a ``__manifest__.py``
    or legacy ``__openerp__.py`` file. The search walks sub-directories up to
    *max_depth* levels deep to avoid scanning huge trees.

    Directories starting with ``.`` or ``__`` are ignored.
    """

    addons = []

    def _walk(current, depth):
        if depth > max_depth:
            return
        for child in current.iterdir():
            if child.name.startswith(".") or child.name.startswith("__"):
                continue
            if child.is_dir():
                if (child / "__manifest__.py").exists() or (
                    child / "__openerp__.py"
                ).exists():
                    addons.append(child)
                _walk(child, depth + 1)

    _walk(base.resolve(), 0)
    return sorted(addons)


def discover_module_names(base):
    """Return module names found in *base*.

    Returns a sorted list of module names that contain a ``__manifest__.py``
    or ``__openerp__.py`` file.
    """
    return [addon.name for addon in discover_addons_paths(base)]


def get_odoo_data_dir(base):
    """Return the Odoo data directory from ``.odoorc`` or the default location.

    When *base* is None or ``.odoorc`` does not configure ``data_dir``, the
    conventional default ``~/.local/share/Odoo`` is returned (only if it
    exists, otherwise None).
    """
    if base is not None:
        odoo_rc = get_odoo_config_path(base)
        if odoo_rc.exists():
            cfg = configparser.ConfigParser()
            cfg.read(odoo_rc)
            value = cfg.get("options", "data_dir", fallback=None)
            if value:
                return Path(value)
    return DEFAULT_ODOO_DATA_DIR if DEFAULT_ODOO_DATA_DIR.exists() else None


def decode_stderr(stderr):
    """Decode subprocess stderr bytes to text, returning "" when None."""
    return stderr.decode("utf-8", errors="replace") if stderr else ""
