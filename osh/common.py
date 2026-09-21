"""Common helpers shared across Osh core and plugins.

This module hosts backend-agnostic utilities used by multiple plugins and
core commands: project root discovery, path conventions, tool availability
checks, and addon discovery. Functions here are intentionally public (no
leading underscore) since they form the shared library contract between
core and plugins.
"""

import configparser
import importlib.resources
import os
import re
import shlex
import shutil
import subprocess
import threading
from pathlib import Path

import click

from . import config, echo

DEFAULT_ODOO_DATA_DIR = Path.home() / ".local" / "share" / "Odoo"


def get_venv_bin(base):
    """Return the virtualenv binary directory for *base*."""
    return Path(base) / ".venv" / ("Scripts" if os.name == "nt" else "bin")


def venv_env(base):
    """Return the environment additions that activate the project's ``.venv``.

    The result puts ``.venv/bin`` first on ``PATH`` and sets ``VIRTUAL_ENV``.
    Callers merge it into the environment passed to spawned processes;
    ``os.environ`` is never mutated.

    Raises a ``click.ClickException`` if the virtualenv does not exist.
    """
    venv_bin = get_venv_bin(base)
    if not venv_bin.is_dir():
        raise click.ClickException(
            "No virtualenv found. Run `osh venv init` to create one."
        )
    venv_path = str(venv_bin)
    path = os.environ.get("PATH", "")
    if venv_path not in path.split(os.pathsep):
        path = f"{venv_path}{os.pathsep}{path}"
    return {"PATH": path, "VIRTUAL_ENV": str(venv_bin.parent)}


def merged_env(*updates):
    """Return ``os.environ`` overlaid with each of the *updates* dicts.

    This is the single place where the environment for spawned processes is
    assembled: commands never mutate ``os.environ`` — they pass the merged
    result explicitly (``env=`` arguments, ``os.execvpe``).
    """
    env = dict(os.environ)
    for update in updates:
        env.update(update)
    return env


def find_shell():
    """Return a shell to launch for an interactive environment session."""
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
    git root. If the git root itself has no ``.osh``, the search continues
    upward through parent directories until finding an ``.osh`` directory
    or reaching the user's home directory. This supports running from inside
    a git submodule of the actual project.

    When not inside a git repository, falls back to walking up from *start*
    looking for a ``.osh`` directory.

    When *required* is True, print an informational message and exit if no
    project is found, instead of returning None.
    """
    start = (start or Path.cwd()).resolve()
    home = Path.home().resolve()

    # Inside a git repo the search starts at the repo root (``.osh`` lives
    # there); outside git it starts at *start* itself.
    git_root = _find_git_root(start)
    walk_from = git_root if git_root is not None else start
    for p in [walk_from] + list(walk_from.parents):
        if (p / ".osh").exists():
            return p
        if p == home:
            break
    if required:
        _not_in_project()
    return None


def _not_in_project():
    """Print a helpful message and exit when no Osh project is found."""
    echo.info(
        "Not inside an Osh project. "
        "Run 'osh venv init <version>' or 'osh docker init <version>' to create one."
    )
    raise SystemExit(0)


def find_enclosing_project(target):
    """Return the nearest strict ancestor of *target* containing ``.osh``.

    Unlike :func:`find_project_root`, *target* itself is never considered —
    a project being re-initialised does not count as its own enclosing
    project — and the walk starts at the parent regardless of git roots:
    for the "would this new project be nested inside another one" question,
    every ancestor directory is relevant.

    Stops at the user's home directory, like ``find_project_root``; when
    *target* is the home directory itself there is no enclosing project.
    """
    target = Path(target).resolve()
    home = Path.home().resolve()
    if target == home:
        return None
    for p in target.parents:
        if (p / ".osh").exists():
            return p
        if p == home:
            break
    return None


def _is_git_repo(path):
    """Return True when *path* looks like a usable git repository root.

    A ``.git`` file (worktree or submodule pointer) is trusted as-is; a
    ``.git`` directory must contain ``HEAD``, so an empty or incomplete
    ``.git`` does not count.
    """
    git = Path(path) / ".git"
    if git.is_file():
        return True
    return (git / "HEAD").exists()


def find_project_repos(base, *, max_depth=4):
    """Return the git repositories that make up the project rooted at *base*.

    When *base* is itself a git repository, ``[base]`` is returned. Otherwise
    sub-directories are scanned up to *max_depth* levels for repositories,
    supporting projects that are a collection of repositories instead of a
    single one. Repositories are not descended into, so git submodules are
    not reported.

    Directories starting with ``.`` or ``__`` are ignored.
    """
    base = Path(base)
    if _is_git_repo(base):
        return [base]

    repos = []

    def _walk(current, depth):
        if depth > max_depth:
            return
        for child in current.iterdir():
            if child.name.startswith(".") or child.name.startswith("__"):
                continue
            if child.is_dir():
                if _is_git_repo(child):
                    repos.append(child)
                else:
                    _walk(child, depth + 1)

    _walk(base, 0)
    return sorted(repos)


def find_nested_repos(base, *, max_depth=4):
    """Return git repositories nested inside the git-rooted project *base*.

    Embedded clones — repositories below a repository root that are not
    submodules — are part of the working environment (e.g. source checkouts
    like ``odoo``/``enterprise``/``design-themes``) and follow ``osh switch``.
    Submodules are pinned to commits by the parent repository and are
    excluded — ``git submodule update`` handles them.

    ``.osh`` is descended despite its dot-prefix — managed source clones
    live there — while other dot-directories, ``__``-prefixed directories,
    ``node_modules`` and symlinks are skipped. Found repositories are not
    descended into. Returns ``[]`` when *base* is not a repository.
    """
    base = Path(base)
    if not _is_git_repo(base):
        return []
    submodules = {(base / path).resolve() for path in _gitmodules_paths(base)}

    repos = []

    def _walk(current, depth):
        if depth > max_depth:
            return
        try:
            children = list(current.iterdir())
        except OSError:
            return
        for child in children:
            if child.is_symlink() or not child.is_dir():
                continue
            if child.name.startswith("__") or child.name == "node_modules":
                continue
            if child.name.startswith(".") and child.name != ".osh":
                continue
            if _is_git_repo(child):
                if child.resolve() not in submodules:
                    repos.append(child)
                continue
            _walk(child, depth + 1)

    _walk(base, 0)
    return sorted(repos)


def _gitmodules_paths(base):
    """Return the submodule paths declared in *base*'s ``.gitmodules``."""
    cfg = configparser.ConfigParser(interpolation=None)
    try:
        cfg.read(base / ".gitmodules", encoding="utf-8")
    except (OSError, configparser.Error):
        return []
    return [
        path
        for section in cfg.sections()
        if section.startswith("submodule ")
        for path in [cfg.get(section, "path", fallback=None)]
        if path
    ]


def find_nested_projects(base, *, max_depth=4):
    """Return directories below *base* that contain their own ``.osh``.

    Mirrors the ``find_project_repos`` walk: directories starting with ``.``
    or ``__`` are ignored, a directory found to contain ``.osh`` is recorded
    without descending further, and git repositories are not descended into
    — a ``.osh`` below a repository root is never selected by
    ``find_project_root`` anyway, which is also why *base* itself being a
    repository means nothing below it can be a nested project. A missing or
    non-directory *base* returns an empty list, and directories that cannot
    be listed are skipped. ``node_modules`` is never descended into.
    """
    base = Path(base)
    if not base.is_dir() or _is_git_repo(base):
        return []

    nested = []

    def _walk(current, depth):
        if depth > max_depth:
            return
        try:
            for child in current.iterdir():
                if child.name.startswith((".", "__")) or child.name == "node_modules":
                    continue
                if child.is_dir():
                    if (child / ".osh").exists():
                        nested.append(child)
                    elif not _is_git_repo(child):
                        _walk(child, depth + 1)
        except OSError:
            return

    _walk(base, 0)
    return sorted(nested)


def git_current_branch(path):
    """Return the current branch of the git repository at *path*, or None."""
    returncode, branch, _ = run_subprocess(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=path,
    )
    if returncode != 0 or not branch:
        return None
    return branch.strip()


def get_odoo_config_path(base):
    """Return path to the Odoo configuration file (.odoorc) in the project root."""
    return base / ".odoorc"


def get_osh_odoo_config_path(base):
    """Return path to the Osh-managed Odoo configuration file (.osh/odoo.conf)."""
    return base / ".osh" / "odoo.conf"


def get_odoo_port(base):
    """Return the Odoo HTTP port configured for *base* (default ``8069``).

    Reads ``http_port`` (or the legacy ``xmlrpc_port``) from the first
    existing of ``.osh/odoo.conf`` and ``.odoorc``.
    """
    for conf in (get_osh_odoo_config_path(base), get_odoo_config_path(base)):
        if not conf.exists():
            continue
        cfg = configparser.ConfigParser()
        cfg.read(conf, encoding="utf-8")
        if not cfg.has_section("options"):
            continue
        for key in ("http_port", "xmlrpc_port"):
            value = cfg["options"].get(key)
            if value and str(value).strip().isdigit():
                return int(str(value).strip())
    return 8069


def has_arg(args, long, short=None):
    """Return True if *args* contains the given long (and optional short) option."""
    for arg in args:
        if arg == long or arg.startswith(f"{long}="):
            return True
        if short and (
            arg == short
            or arg.startswith(f"{short}=")
            or _is_short_with_value(arg, short)
        ):
            return True
    return False


def _is_short_with_value(arg, short):
    """Return True when *arg* is *short* followed by a value (not another flag)."""
    return arg.startswith(short) and len(arg) > len(short) and arg[len(short)] != "-"


def odoo_http_port(args):
    """Return the ``-p``/``--http-port`` value in Odoo arguments, or None.

    Accepts the ``-p 8080``, ``-p8080``, ``-p=8080``, ``--http-port 8080``
    and ``--http-port=8080`` forms; the last occurrence wins, matching
    Odoo's own option parsing.
    """
    value = None
    for i, arg in enumerate(args):
        if arg in ("-p", "--http-port") and i + 1 < len(args):
            value = args[i + 1]
        elif arg.startswith("--http-port="):
            value = arg.split("=", 1)[1]
        elif arg.startswith("-p") and len(arg) > 2:
            value = arg[3:] if arg[2] == "=" else arg[2:]
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def format_cmd(args):
    """Return *args* as a single shell-quoted command line string."""
    return " ".join(shlex.quote(str(a)) for a in args)


def _stream_output(pipe, err=False):
    """Read *pipe* line-by-line and echo it to the user."""
    for line in iter(pipe.readline, ""):
        if not line:
            break
        echo.info(line.rstrip("\r\n"), err=err)
    pipe.close()


def run_command(
    args,
    *,
    cwd=None,
    env=None,
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
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=text,
            )
        except FileNotFoundError as exc:
            raise click.ClickException(
                f"Command not found: {format_cmd(args)}"
            ) from exc

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
            raise click.ClickException(
                f"Command failed (exit {returncode}): {format_cmd(args)}"
            )
        return result

    try:
        return subprocess.run(
            args,
            cwd=cwd,
            env=env,
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


def run_subprocess(
    args,
    *,
    cwd=None,
    env=None,
    text=True,
    stdout=None,
    stderr=None,
    error_msg=None,
    dry_run=False,
    silent=False,
    **kwargs,
):
    """Run *args* and return ``(returncode, stdout, stderr)``.

    Stdout and stderr are captured by default. Pass ``stdout`` or ``stderr``
    explicitly to override the capture behaviour (e.g. ``stderr=subprocess.DEVNULL``).
    When the executable is missing, ``returncode`` is ``None`` and ``stderr``
    contains a "command not found" message.

    If *error_msg* is given, a non-zero exit code or a missing executable raises
    a ``click.ClickException`` using that message.

    If *dry_run* is True, the command is not executed; ``(0, "", "")`` is
    returned after printing the command that would have run.

    If *silent* is True, both stdout and stderr are discarded to ``/dev/null``.
    """
    cmd = format_cmd(args)
    if dry_run:
        echo.info(f"Would run: {cmd}", err=True)
        return 0, "", ""

    run_kwargs = dict(cwd=cwd, env=env, text=text)
    if silent:
        run_kwargs["stdout"] = subprocess.DEVNULL
        run_kwargs["stderr"] = subprocess.DEVNULL
    elif stdout is not None or stderr is not None:
        run_kwargs["stdout"] = stdout if stdout is not None else subprocess.PIPE
        run_kwargs["stderr"] = stderr if stderr is not None else subprocess.PIPE
    else:
        run_kwargs["capture_output"] = True
    run_kwargs.update(kwargs)

    try:
        result = subprocess.run(args, **run_kwargs)
    except FileNotFoundError as exc:
        if error_msg:
            raise click.ClickException(f"{error_msg}: command not found") from exc
        return None, "", f"Command not found: {cmd} ({exc})"

    echo.debug(f"exit {result.returncode}: {cmd}")

    if error_msg and result.returncode != 0:

        def _as_text(data):
            if data is None:
                return ""
            if isinstance(data, bytes):
                return data.decode("utf-8", errors="replace")
            return data

        output = "\n".join(
            part for part in [_as_text(result.stdout), _as_text(result.stderr)] if part
        )
        message = f"{error_msg}\nCommand: {cmd}"
        if output:
            message += f"\n{output}"
        raise click.ClickException(message)
    return result.returncode, result.stdout or "", result.stderr or ""


def run_shell_pipeline(
    commands,
    *,
    stdout=None,
    env=None,
    text=False,
    error_msg=None,
    not_found_msg=None,
):
    """Run a shell pipeline of commands and return ``(returncode, stdout, stderr)``.

    Each command in *commands* is an iterable of arguments.  The pipeline is
    built with `shlex.quote` and executed via ``sh -c``.  Stderr is captured
    and returned as text unless *text* is ``False``.

    If *error_msg* is given, a missing executable or a non-zero exit code raises
    a ``click.ClickException`` using that message.  *not_found_msg* overrides
    the message used when the executable is missing.
    """
    pipeline = " | ".join(format_cmd(cmd) for cmd in commands)
    returncode, out, stderr = run_subprocess(
        ["sh", "-c", pipeline], stdout=stdout, env=env, text=text
    )
    if error_msg:
        if returncode is None:
            raise click.ClickException(
                not_found_msg or f"{error_msg}: command not found"
            )
        if returncode != 0:
            raise click.ClickException(f"{error_msg}: {stderr}")
    return returncode, out, stderr


def discover_addons_paths(base, *, max_depth=9):
    """Return a list of addon directories under *base*.

    An *addon* is recognised if the directory contains a ``__manifest__.py``
    or legacy ``__openerp__.py`` file. The search walks sub-directories up to
    *max_depth* levels deep to avoid scanning huge trees. Symlinks are
    followed like regular directories.

    Directories starting with ``.`` or ``__`` are ignored. A module
    directory is a leaf: its contents are never scanned. Addons nested
    inside another addons directory — e.g. the OCA ``setup/<pkg>/odoo/addons``
    symlink dirs — are dropped when an outer addons directory already
    provides a module with the same name.
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
                else:
                    _walk(child, depth + 1)

    base = base.resolve()
    _walk(base, 0)
    # The walk root doesn't count as a nesting ancestor: a module sitting at
    # the project root makes *base* an addons dir, but shouldn't classify
    # every dir below it as nested.
    parents = {addon.parent for addon in addons} - {base}

    def _nested(addon):
        return any(parent in parents for parent in addon.parent.parents)

    outer_names = {addon.name for addon in addons if not _nested(addon)}
    # A nested addons dir is redundant when an outer dir already provides a
    # module with the same name (the OCA ``setup/`` symlinks). A nested dir
    # with unique modules is kept: dropping it would make them unreachable.
    return sorted(
        addon for addon in addons if not _nested(addon) or addon.name not in outer_names
    )


def discover_module_names(base):
    """Return module names found in *base*.

    Returns a sorted list of module names that contain a ``__manifest__.py``
    or ``__openerp__.py`` file.
    """
    return sorted({addon.name for addon in discover_addons_paths(base)})


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


def get_user_neutralize_dir():
    """Return the global user directory containing default neutralization scripts."""
    return config.get_user_config_path().parent / "neutralize"


def _major_version_from_string(version):
    """Return the first integer found in *version*, or None if not found."""
    if not version:
        return None
    match = re.search(r"(\d+)", str(version))
    return int(match.group(1)) if match else None


def setup_project_neutralize_scripts(target, version):
    """Populate ``.osh/neutralize`` with default and user-provided SQL scripts.

    Bundled defaults are written first and refreshed on every init, so later
    fixes to them reach existing projects; user scripts from
    ``~/.config/osh/neutralize/`` are copied last, so a same-named user script
    always wins. ``900_clear_assets`` drops the generated asset bundles a
    restored dump carries, whose filestore files are usually missing locally.
    For Odoo versions older than 16.0, the bundled fallback SQL script is also
    copied so ``osh db restore`` can neutralize the database without the
    ``odoo-bin neutralize`` subcommand.
    """
    neutralize_dir = Path(target) / ".osh" / "neutralize"
    neutralize_dir.mkdir(parents=True, exist_ok=True)

    # Numbered so the bundled defaults keep their order around user scripts:
    # neutralization first, asset cleanup last.
    major = _major_version_from_string(version)
    if major is not None and major < 16:
        _copy_bundled_neutralize_script(
            neutralize_dir, "neutralize_fallback.sql", "000_osh_default.sql"
        )
    _copy_bundled_neutralize_script(
        neutralize_dir, "clear_assets.sql", "900_clear_assets.sql"
    )

    user_dir = get_user_neutralize_dir()
    if user_dir.is_dir():
        for src in sorted(user_dir.glob("*.sql")):
            shutil.copy2(src, neutralize_dir / src.name)
            echo.info(f"Copied neutralization script: {src.name}", err=True)


def _copy_bundled_neutralize_script(neutralize_dir, resource, name):
    """Install the bundled *resource* as *name*.

    An existing copy is refreshed so fixes to the bundled script reach the
    project on re-init; an identical file is left alone.
    """
    content = importlib.resources.read_text("osh.data", resource)
    destination = neutralize_dir / name
    if destination.exists() and destination.read_text(encoding="utf-8") == content:
        return
    destination.write_text(content, encoding="utf-8")
    echo.info(f"Copied default neutralization script: {name}", err=True)
