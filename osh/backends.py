"""Backend abstractions for Osh commands.

Backends allow plugins to replace the default host-venv execution model with
other targets, such as Docker or remote containers, while keeping the same
``osh shell``/``osh odoo`` user interface. Each backend also gets an
``osh <name>`` command group (``init``, ``doctor``, ``stop``) — see
``osh.commands.backend_cmd.backend_group``.

``NoneBackend`` is the built-in default backend, used when no other backend
is configured: it runs commands directly on the host.
"""

import os
import shlex
import shutil
import signal
import time
from abc import ABC
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

import click

from . import echo
from .common import (
    find_shell,
    format_cmd,
    get_odoo_config_path,
    get_odoo_data_dir,
    get_odoo_port,
    get_osh_odoo_config_path,
    has_arg,
    merged_env,
    run_command,
    run_subprocess,
)
from .utils.odoo_layout import find_odoo_executable
from .utils.version import get_version_from_executable

# ``NoneBackend.stop`` timings: how long to wait for Odoo to release its
# HTTP port after SIGTERM before escalating to SIGKILL, how long to wait
# for SIGKILL to take effect, and how often to re-check the port.
_SIGTERM_GRACE_SECONDS = 5.0
_SIGKILL_GRACE_SECONDS = 1.0
_PORT_POLL_INTERVAL_SECONDS = 0.1


def copy_odoo_rc_to_osh_conf(base):
    """Copy .odoorc to .osh/odoo.conf if .odoorc exists and .osh/odoo.conf doesn't.

    Returns the path to ``.osh/odoo.conf`` regardless of whether a copy happened.
    """
    odoo_rc = base / ".odoorc"
    osh_odoo_conf = get_osh_odoo_config_path(base)
    if odoo_rc.exists() and not osh_odoo_conf.exists():
        shutil.copy(odoo_rc, osh_odoo_conf)
        echo.info("Copied .odoorc to .osh/odoo.conf", err=True)
    return osh_odoo_conf


@dataclass
class EnvSpec:
    """Structured environment invocation passed to ``Backend.env()``.

    ``argv`` is the command and arguments to execute inside the target
    environment. ``env`` is a mapping of extra environment variables that the
    backend should expose before running the command. ``input`` is optional
    stdin content for captured runs. ``stdin`` is an optional readable binary
    file object used as the command's stdin — it carries large payloads such
    as database dumps without buffering them in memory. ``db_name`` and
    ``config_path`` are informational hints passed to ``odoo.pre_env`` hooks
    (e.g. a hook may parse the generated config) — backends do not act on them.
    """

    argv: list = field(default_factory=list)
    env: dict = field(default_factory=dict)
    input: str = None
    stdin: BinaryIO = None
    db_name: str = None
    config_path: str = None


class Backend(ABC):
    """Unified base class for Osh init and execution backends."""

    backend_type = "backend"
    name = ""
    label = ""
    description = ""
    help_text = ""
    # True when Odoo runs from a host-resolved executable (``.venv/bin/odoo``,
    # ``odoo-bin``, PATH) rather than a backend-managed command name.
    host_executable = False

    @classmethod
    def get_init_options(cls):
        """Return backend-specific options for the ``osh <name> init`` command."""
        return []

    def detect_odoo_version(self, base):
        """Return the installed Odoo version for *base*, or None if unknown.

        The default reads the version from the checked-out Odoo sources;
        backends override this to try backend-specific detection first.
        """
        from .utils.version import get_version_from_sources

        return get_version_from_sources(base)

    def diagnose_sections_for_phase(self, phase):
        """Return the diagnose sections to run for *phase*.

        ``None`` means "all sections". This is used by ``osh <backend> init``
        and ``osh odoo`` to skip expensive checks that are only useful for a
        full ``osh <backend> doctor`` report.
        """
        return None

    def _add_init_plans(self, todo):
        """Add backend-specific init plans to the TodoPlan.

        Backends can override this to add their own planned actions.
        The default implementation adds no plans.
        """
        pass

    def odoo_data_dir(self, base):
        """Return Odoo's data dir as seen inside the backend environment.

        Host backends return the host path; containerized backends return the
        path inside the container (e.g. a named volume mount), which may not
        exist on the host at all. Used by the database helpers to locate the
        filestore. Returns None when it cannot be determined.
        """
        return get_odoo_data_dir(base)

    def build_addons_paths(self, base, *, include_themes=False):
        """Return a list of addon paths for *base*.

        Includes the Odoo core addons directory, Enterprise, optionally
        design-themes, and discovered project addon parent directories.
        The default implementation returns host paths for local backends.
        """
        from .utils.odoo_layout import build_addons_paths as _build_addons_paths

        return _build_addons_paths(base, include_themes=include_themes)

    def diagnose(
        self,
        base,
        ctx=None,
        *,
        sections=None,
        **options,
    ):
        """Inspect the project and system for the active target.

        *sections* is an optional list of section names to detect. When omitted,
        backends should detect everything. Callers such as ``osh <backend> init``
        and ``osh odoo`` can use it to avoid expensive checks that are not needed
        for their phase.

        Returns a ``Diagnostics`` object that ``osh <backend> doctor`` reports,
        ``osh <backend> init`` uses to plan actions and ask for confirmation,
        and ``osh odoo`` uses to check prerequisites.
        """
        raise NotImplementedError

    def init(
        self,
        target,
        *,
        version="",
        edition="ce",
        dry_run=False,
        todo=None,
        **options,
    ):
        """Set up the environment. Return ``True`` if ready for use.

        *todo* is the ``TodoPlan`` progress tracker ``osh <backend> init``
        passes so backends can announce steps via ``todo.start()`` while running.
        """
        raise NotImplementedError

    def env(
        self,
        ctx,
        base,
        env_spec,
        *,
        dry_run=False,
        **options,
    ):
        """Run a command inside the target environment using the supplied ``EnvSpec``.

        ``env_spec`` is an ``EnvSpec`` instance. The backend prepares the
        environment (e.g.
        activates the local virtualenv or enters the Docker container) and
        executes ``env_spec.argv`` with ``env_spec.env`` applied. When ``argv`` is
        empty, an interactive shell is launched.

        Supported *options*:

        - ``wait=True``: wait for the command to exit, streaming its output,
          instead of replacing the current process.
        - ``capture=True`` (implies ``wait``): return
          ``(returncode, stdout, stderr)`` instead of streaming, so callers
          such as the database helpers can inspect the result.
        - ``stdout``/``text``: forwarded to the capture subprocess call.
        """
        raise click.ClickException(
            f"Backend '{self.name}' does not support environment execution."
        )

    def db_env(
        self,
        ctx,
        base,
        env_spec,
        *,
        dry_run=False,
        **options,
    ):
        """Run a command inside the database environment.

        The default delegates to :meth:`env`: host-like backends share one
        environment for Odoo and PostgreSQL, so the contexts coincide.
        Backends with a separate database service (e.g. Docker's Compose
        ``db`` service) override this to target that service.
        """
        return self.env(ctx, base, env_spec, dry_run=dry_run, **options)

    @classmethod
    def get_stop_options(cls):
        """Additional ``click.Option`` objects for ``osh <name> stop``."""
        return []

    def stop(self, ctx, base, **options):
        """Stop resources the backend may have left running.

        The default is a no-op for backends without persistent state, so
        ``osh <backend> stop`` is always safe to run.
        """
        echo.info(f"Nothing to stop for the '{self.name}' backend.", err=True)


class NoneBackend(Backend):
    """Default backend: run commands directly on the host.

    The ``none`` backend manages no environment — it execs the resolved
    Odoo executable (``.venv/bin/odoo``, a source checkout, or whatever is
    on ``PATH``) with the project environment applied. Managed targets such
    as ``venv`` subclass it and layer their environment on top.
    """

    name = "none"
    label = "Host (no backend)"
    backend_type = "backend"
    host_executable = True
    description = "Run Odoo directly on the host (default)."
    help_text = (
        "Runs commands directly on the host with the project's Odoo config "
        "and database environment applied — no virtualenv or container is "
        "managed. Odoo itself is resolved from ``.venv/bin``, ``.osh/odoo`` "
        "sources, or ``PATH``."
    )

    _DIAGNOSE_SECTIONS = (
        "odoo_executable",
        "odoo_version",
        "config",
        "addons",
    )

    def diagnose_sections_for_phase(self, phase):
        """Skip expensive version/addons checks in ``init`` and ``run`` phases."""
        if phase in ("init", "run"):
            return ["odoo_executable", "config"]
        return list(self._DIAGNOSE_SECTIONS)

    def detect_odoo_version(self, base):
        """Return the Odoo version from the local executable or sources."""
        exe = find_odoo_executable(base)
        if exe:
            version = get_version_from_executable(exe)
            if version:
                return version
        return super().detect_odoo_version(base)

    def diagnose(
        self,
        base,
        ctx=None,
        *,
        sections=None,
        **options,
    ):
        # Imported lazily: osh.commands imports this module, so a top-level
        # import of commands.helpers would be circular.
        from .commands.helpers import Diagnostics

        phase = options.get("phase", "doctor")
        d = Diagnostics(self.name, project=base)

        if sections is None:
            sections = self._DIAGNOSE_SECTIONS
        sections = set(sections)

        if "odoo_executable" in sections or "odoo_version" in sections:
            exe = find_odoo_executable(base)
            if "odoo_executable" in sections and exe:
                d.add_info("odoo_executable", str(exe))

            if "odoo_version" in sections:
                odoo_version = self.detect_odoo_version(base)
                if odoo_version:
                    d.add_info("odoo_version", odoo_version)
                elif exe and phase == "doctor":
                    d.add_warning("Could not determine installed Odoo version.")
                elif not exe:
                    if phase == "init":
                        d.add_warning(
                            "Odoo executable not found; "
                            "it will be resolved from PATH at run time."
                        )
                    else:
                        d.add_error("Odoo executable not found. Run 'osh init' first.")

        if "config" in sections:
            odoo_rc = get_odoo_config_path(base)
            osh_conf = get_osh_odoo_config_path(base)
            config = osh_conf if osh_conf.exists() else odoo_rc
            if config.exists():
                d.add_info("odoo_config", str(config))
            elif phase != "run":
                # More informative warning showing both attempted paths
                attempted_paths = [str(osh_conf), str(odoo_rc)]
                d.add_warning(
                    f"Odoo config file not found. Attempted: {', '.join(attempted_paths)}"
                )
            else:
                d.add_info("odoo_config", "<generated by osh shell>")

        if "addons" in sections:
            addons_paths = self.build_addons_paths(base, include_themes=True)
            d.add_info("addons_directories", len(addons_paths))

        return d

    def _add_init_plans(self, todo):
        """The ``none`` backend manages no environment — nothing to install."""
        todo.add_plan(
            "Nothing to install: the 'none' backend runs commands on the host"
        )

    def init(
        self,
        target,
        *,
        version="",
        edition="ce",
        dry_run=False,
        todo,
        **options,
    ):
        """Register the project; the ``none`` backend manages no environment."""
        return True

    def _base_env(self, base, capture):
        """Return environment layered on top of the host env (none here).

        Managed backends (e.g. ``venv``) override this to inject their
        environment. *capture* marks internal subprocess calls so backends
        may skip strict environment requirements for them.
        """
        return {}

    def env(
        self,
        ctx,
        base,
        env_spec,
        *,
        dry_run=False,
        **options,
    ):
        wait = options.pop("wait", False)
        capture = options.pop("capture", False)

        env = merged_env(self._base_env(base, capture), env_spec.env)

        args = list(env_spec.argv)
        if not args:
            args = [find_shell()]
        elif "ODOO_RC" not in env_spec.env and not has_arg(args, "--addons-path"):
            # Subcommands such as ``odoo shell`` or ``odoo neutralize`` do not
            # use the generated config, so inject the addons path explicitly.
            odoo_exe = Path(args[0]).name
            if odoo_exe in ("odoo-bin", "odoo"):
                addons_paths = self.build_addons_paths(base, include_themes=True)
                if addons_paths:
                    args.insert(
                        1, f"--addons-path={','.join(str(p) for p in addons_paths)}"
                    )

        command = format_cmd(args)
        if dry_run and not capture:
            echo.info(f"Would run: {command}", err=True)
            return
        # Captured calls (read-only probes such as ``psql -c SELECT 1``) still
        # execute in dry-run mode so the preview can give real answers.

        if wait or capture:
            if capture:
                # Internal calls (probes, db helpers) are not user commands —
                # keep them off the console.
                echo.debug(f"Running: {command}")
                return run_subprocess(
                    args,
                    env=env,
                    input=env_spec.input,
                    stdin=env_spec.stdin,
                    stdout=options.get("stdout"),
                    text=options.get("text", True),
                )
            echo.info(f"Running: {command}", err=True)
            run_command(args, env=env, check=True, stream=True)
            return None

        echo.info(f"Running: {command}", err=True)

        try:
            os.execvpe(args[0], args, env)
        except OSError as exc:
            raise click.ClickException(f"Could not run {args[0]}: {exc}") from exc

    def stop(self, ctx, base, **options):
        """Stop an Odoo process left listening on the project's HTTP port."""
        port = get_odoo_port(base)
        listeners = _port_listeners(port)
        if not listeners:
            echo.info(f"Nothing to stop: no process listening on port {port}.")
            return
        for pid in listeners:
            cmdline = _pid_command(pid)
            if not _looks_like_odoo(cmdline):
                echo.warning(
                    f"Port {port} is held by '{cmdline or 'unknown'}' "
                    f"(pid {pid}), which does not look like Odoo — "
                    "leaving it alone."
                )
                continue
            echo.info(f"Stopping Odoo process {pid} ({cmdline})...", err=True)
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError as exc:
                echo.warning(f"Could not stop pid {pid}: {exc}")
                continue
            if _wait_for_port_release(port, pid):
                continue
            # Re-check identity before escalating: during the grace period the
            # original process may have exited and the kernel may have reused
            # its pid for something unrelated, which must not be killed.
            if not _looks_like_odoo(_pid_command(pid)):
                echo.warning(
                    f"Process {pid} no longer looks like Odoo (pid reused?) — "
                    "not sending SIGKILL."
                )
                continue
            echo.info(f"Process {pid} ignored SIGTERM; sending SIGKILL.", err=True)
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError as exc:
                echo.warning(f"Could not kill pid {pid}: {exc}")
                continue
            if not _wait_for_port_release(port, pid, timeout=_SIGKILL_GRACE_SECONDS):
                echo.warning(
                    f"Odoo process {pid} is still listening on port {port} "
                    "after SIGKILL."
                )


def _wait_for_port_release(port, pid, timeout=_SIGTERM_GRACE_SECONDS):
    """Return True once *pid* no longer listens on *port*, False on timeout.

    Polling the port rather than the PID keeps this correct when the dead
    process lingers as a zombie: a zombie still exists but its sockets are
    already closed.
    """
    deadline = time.monotonic() + timeout
    while pid in _port_listeners(port):
        if time.monotonic() >= deadline:
            return False
        time.sleep(_PORT_POLL_INTERVAL_SECONDS)
    return True


def _port_listeners(port):
    """Return PIDs of processes listening on TCP *port* (best effort)."""
    lsof = shutil.which("lsof")
    if lsof:
        returncode, out, err = run_subprocess(
            [lsof, "-nP", f"-tiTCP:{int(port)}", "-sTCP:LISTEN"]
        )
        if returncode != 0:
            # lsof exits 1 both for "nothing found" and for real failures
            # (e.g. denied /proc access); only the latter writes to stderr.
            # Reporting it matters: a silent [] would make the caller claim
            # the port is free while Odoo is still holding it.
            if (err or "").strip():
                echo.warning(f"Could not check port {port} with lsof: {err.strip()}")
            return []
        return [int(p) for p in out.split() if p.strip().isdigit()]

    fuser = shutil.which("fuser")
    if fuser:
        # fuser prints the PIDs on stdout (and the port label on stderr, so
        # stderr is no failure signal here). Exit 1 means "no process".
        returncode, out, err = run_subprocess([fuser, f"{int(port)}/tcp"])
        if returncode not in (0, 1):
            echo.warning(
                f"Could not check port {port} with fuser: "
                f"{(err or '').strip() or f'exit {returncode}'}"
            )
            return []
        if returncode != 0:
            return []
        return [int(p) for p in (out or "").split() if p.isdigit()]

    return _proc_port_listeners(port)


def _proc_port_listeners(port):
    """Linux fallback: find PIDs listening on *port* by scanning ``/proc``."""
    inodes = set()
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = Path(table).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            fields = line.split()
            # local_address is "HEXIP:HEXPORT"; state 0A is LISTEN.
            if len(fields) < 10 or fields[3] != "0A":
                continue
            try:
                if int(fields[1].rsplit(":", 1)[1], 16) == int(port):
                    inodes.add(fields[9])
            except (ValueError, IndexError):
                continue
    if not inodes:
        return []

    # Built once: this is compared against every fd of every process, and
    # the caller polls it repeatedly while waiting for the port to clear.
    socket_links = {f"socket:[{i}]" for i in inodes}
    pids = []
    proc = Path("/proc")
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            for fd in (entry / "fd").iterdir():
                try:
                    if os.readlink(fd) in socket_links:
                        pids.append(int(entry.name))
                        break
                except OSError:
                    continue
        except OSError:
            continue
    return pids


def _pid_command(pid):
    """Return the command line of *pid*, or an empty string."""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        if raw:
            return raw.replace(b"\0", b" ").decode(errors="replace").strip()
    except OSError:
        pass
    returncode, out, _ = run_subprocess(["ps", "-p", str(int(pid)), "-o", "args="])
    return out.strip() if returncode == 0 else ""


_ODOO_EXECUTABLES = ("odoo", "odoo-bin", "odoo.py", "odoo.sh")
_PYTHON_EXECUTABLES = ("python", "python3")


def _looks_like_odoo(cmdline):
    """Return True when *cmdline* invokes Odoo.

    Matches a direct ``odoo``/``odoo-bin``/``odoo.py``/``odoo.sh``
    executable as well as ``python -m odoo``, where ``odoo`` is a module
    argument rather than the executable. Deliberately conservative: a
    false negative only leaves a process running, while a false positive
    would kill something that merely happens to hold the port.
    """
    try:
        tokens = shlex.split(cmdline or "")
    except ValueError:
        tokens = (cmdline or "").split()
    for index, token in enumerate(tokens):
        name = Path(token).name
        if name in _ODOO_EXECUTABLES:
            return True
        if name in _PYTHON_EXECUTABLES and tokens[index + 1 : index + 3] == [
            "-m",
            "odoo",
        ]:
            return True
    return False
