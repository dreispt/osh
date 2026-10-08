"""Runtime abstractions for Osh commands.

Runtimes allow plugins to replace the default host-venv execution model with
other targets, such as Docker or remote containers, while keeping the same
``osh shell``/``osh odoo`` user interface. Runtime lifecycle plugs into the
core commands: ``osh init --runtime=<name>`` drives ``init`` and records the
runtime as active, and ``osh stop`` drives ``stop``/``stop_by_name``/
``stop_all``.

``HostRuntime`` (the ``host`` runtime) is the built-in default, used when
no other runtime is configured: it runs commands directly on the host. The
legacy ``none`` name still resolves to it.

``osh.backends`` and the ``Backend``/``HostBackend`` class names are
deprecated aliases kept for plugins written against the old backend API.
"""

import hashlib
import json
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
from .config import get_project_config
from .utils.odoo_layout import find_odoo_executable
from .utils.version import get_version_from_executable

# ``HostRuntime.stop`` timings: how long to wait for Odoo to release its
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
    """Structured environment invocation passed to ``Runtime.env()``.

    ``argv`` is the command and arguments to execute inside the target
    environment. ``env`` is a mapping of extra environment variables that the
    runtime should expose before running the command. ``input`` is optional
    stdin content for captured runs. ``stdin`` is an optional readable binary
    file object used as the command's stdin — it carries large payloads such
    as database dumps without buffering them in memory. ``db_name`` and
    ``config_path`` are informational hints exposed to ``odoo`` operation
    extensions in ``pre_env`` (e.g. an extension may parse the generated
    config) — runtimes do not act on them.
    """

    argv: list = field(default_factory=list)
    env: dict = field(default_factory=dict)
    input: str = None
    stdin: BinaryIO = None
    db_name: str = None
    config_path: str = None


@dataclass
class BuildInputs:
    """The inputs a built environment artifact was produced from.

    ``paths`` are the files whose contents feed the artifact's fingerprint;
    ``spec`` is an optional opaque value — e.g. a resolved Compose
    ``build:`` mapping — hashed alongside them.
    """

    paths: list = field(default_factory=list)
    spec: object = None


class Runtime(ABC):
    """Unified base class for Osh init and execution runtimes."""

    runtime_type = "runtime"
    # Deprecated alias kept for plugins written against the backend API.
    backend_type = "backend"
    name = ""
    label = ""
    description = ""
    help_text = ""
    # True when Odoo runs from a host-resolved executable (``.venv/bin/odoo``,
    # ``odoo-bin``, PATH) rather than a runtime-managed command name.
    host_executable = False

    @classmethod
    def get_init_options(cls):
        """Return runtime-specific options merged into ``osh init``."""
        return []

    def odoo_command(self, base):
        """Return the configured Odoo run command as an argv list, or None.

        The ``host`` runtime is a "bring your own Odoo" runtime: it can be
        given the run command a system init file would use (e.g.
        ``/usr/bin/odoo -c /etc/odoo/odoo.conf``). The default returns
        ``None`` — ``osh odoo`` resolves the executable itself.
        """
        return None

    def base_odoo_conf(self, base):
        """Return a configured base Odoo config file for *base*, or None.

        When set, the generated per-branch config seeds from this file and
        the project's ``.osh/odoo.conf``/``.odoorc`` layers on top of it.
        """
        return None

    def detect_odoo_version(self, base):
        """Return the installed Odoo version for *base*, or None if unknown.

        The default reads the version from the checked-out Odoo sources;
        runtimes override this to try runtime-specific detection first.
        """
        from .utils.version import get_version_from_sources

        return get_version_from_sources(base)

    def diagnose_sections_for_phase(self, phase):
        """Return the diagnose sections to run for *phase*.

        ``None`` means "all sections". This is used by ``osh init --runtime``
        and ``osh odoo`` to skip expensive checks that are only useful for a
        full diagnostics report.
        """
        return None

    def _add_init_plans(self, todo):
        """Add runtime-specific init plans to the TodoPlan.

        Runtimes can override this to add their own planned actions.
        The default implementation adds no plans.
        """
        pass

    def odoo_data_dir(self, base):
        """Return Odoo's data dir as seen inside the runtime environment.

        Host runtimes return the host path; containerized runtimes return the
        path inside the container (e.g. a named volume mount), which may not
        exist on the host at all. Used by the database helpers to locate the
        filestore. Returns None when it cannot be determined.
        """
        return get_odoo_data_dir(base)

    def build_addons_paths(self, base, *, include_themes=False):
        """Return a list of addon paths for *base*.

        Includes the Odoo core addons directory, Enterprise, optionally
        design-themes, and discovered project addon parent directories.
        The default implementation returns host paths for local runtimes.
        """
        from .utils.odoo_layout import build_addons_paths as _build_addons_paths

        return _build_addons_paths(base, include_themes=include_themes)

    def _environment_inputs(self, base, **options):
        """Return ``{key: BuildInputs}`` for artifacts this runtime builds.

        Keys name the built artifact (a Compose service, the project
        virtualenv). The default returns ``{}``: runtimes that manage no
        built environment (e.g. ``host``) never report staleness and never
        rebuild.
        """
        return {}

    def _environment_changes(self, base, missing_is_change=False, **options):
        """Return ``{key: [changed input labels]}`` for stale artifacts.

        Inputs are fingerprinted on content and compared with the record
        the last build left in ``.osh/cache/env-fingerprints.json`` — an
        edited file, a removed input or a changed spec all count.
        Artifacts without a record have no baseline yet and are not stale
        (the next build writes it) — unless *missing_is_change* is set,
        as init-time build decisions do: an unrecorded artifact is one
        that still needs building.
        """
        inputs = self._environment_inputs(base, **options)
        if not inputs:
            return {}
        stored = _load_env_fingerprints(base).get(self.name, {})
        changed = {}
        for key, spec in inputs.items():
            previous = stored.get(key)
            current = _fingerprint_inputs(base, spec)
            if not isinstance(previous, dict):
                if missing_is_change:
                    changed[key] = sorted(current)
                continue
            diff = [
                label
                for label, digest in current.items()
                if previous.get(label) != digest
            ]
            diff += [label for label in previous if label not in current]
            if diff:
                changed[key] = sorted(diff)
        return changed

    def _record_environment_fingerprints(self, base, **options):
        """Store the current input fingerprints after a successful build."""
        inputs = self._environment_inputs(base, **options)
        if inputs:
            self._store_environment_fingerprints(
                base,
                {key: _fingerprint_inputs(base, spec) for key, spec in inputs.items()},
            )

    def _store_environment_fingerprints(self, base, records):
        """Merge *records* into the fingerprint store under this runtime."""
        data = _load_env_fingerprints(base)
        data.setdefault(self.name, {}).update(records)
        path = _env_fingerprints_path(base)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")

    def _stale_environment_hint(self):
        """Remediation text appended to the stale-environment warning."""
        return (
            f"Run 'osh init --runtime={self.name}' to refresh the environment "
            "('osh init --fingerprint' accepts the inputs without rebuilding)."
        )

    def _check_stale_environment(self, base, d, **options):
        """Warn when inputs the environment was built from changed since the build."""
        changed = dict.fromkeys(
            label
            for labels in self._environment_changes(base, **options).values()
            for label in labels
        )
        if changed:
            d.add_warning(
                "Files the environment was built from changed after the "
                "build: " + ", ".join(changed) + ". " + self._stale_environment_hint()
            )

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
        runtimes should detect everything. Callers such as ``osh init --runtime``
        and ``osh odoo`` can use it to avoid expensive checks that are not needed
        for their phase.

        Returns a ``Diagnostics`` object that ``osh init --runtime`` uses to
        plan actions and ask for confirmation, and ``osh odoo`` uses to check
        prerequisites.
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

        *todo* is the ``TodoPlan`` progress tracker ``osh init --runtime``
        passes so runtimes can announce steps via ``todo.start()`` while running.
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

        ``env_spec`` is an ``EnvSpec`` instance. The runtime prepares the
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
            f"Runtime '{self.name}' does not support environment execution."
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

        The default delegates to :meth:`env`: host-like runtimes share one
        environment for Odoo and PostgreSQL, so the contexts coincide.
        Runtimes with a separate database service (e.g. Docker's Compose
        ``db`` service) override this to target that service.
        """
        return self.env(ctx, base, env_spec, dry_run=dry_run, **options)

    @classmethod
    def get_stop_options(cls):
        """Additional ``click.Option`` objects for ``osh stop``."""
        return []

    def status(self, ctx, base):
        """Print what this runtime has running for *base*, if anything.

        Called by ``osh init``'s status report on an already-initialised
        project. The default prints nothing — runtimes without persistent
        state have nothing to report.
        """

    def odoo_port(self, base):
        """Return the host port Odoo serves HTTP on for *base*, or None.

        ``osh stop --list`` uses it to report what holds the project's
        port; the default reads the Odoo config files.
        """
        return get_odoo_port(base)

    def port_holders(self, ctx, port, base):
        """Return report lines for this runtime's holders of host *port*.

        ``osh stop --list`` asks every runtime, so the report covers
        holders the active runtime's own ``stop`` cannot see — e.g. a
        Docker container publishing the port of a host-runtime project.
        *base* is the project asking: lines should name the owning
        resource and which ``osh stop`` invocation frees it. The default
        reports no holders.
        """
        return []

    def stop(self, ctx, base, *, dry_run=False, **options):
        """Stop resources the runtime may have left running.

        With *dry_run* — ``osh stop --list`` — report what would be
        stopped instead of stopping it. The default is a no-op for
        runtimes without persistent state, so ``osh stop`` is always
        safe to run.
        """
        echo.info(f"Nothing to stop for the '{self.name}' runtime.", err=True)

    def stop_by_name(self, ctx, name, *, dry_run=False, **options):
        """Stop resources belonging to the Osh project named *name*.

        Backs ``osh stop <name>`` for runtimes whose resources are
        discoverable outside the project directory (e.g. Compose stacks
        identified by container labels); with *dry_run* (``osh stop
        <name> --list``) they are only reported. The default rejects —
        the runtime manages nothing that a name could resolve.
        """
        raise click.ClickException(
            f"The '{self.name}' runtime manages no named resources "
            f"— no stack found for '{name}'."
        )

    def stop_all(self, ctx, *, dry_run=False, **options):
        """Stop every Osh-managed resource of this runtime, host-wide.

        Backs ``osh stop --all``: runtimes whose resources outlive the
        project directory (containers, stray processes) enumerate and stop
        them here; with *dry_run* (``osh stop --all --list``) they are
        only reported. The default is a no-op.
        """


class HostRuntime(Runtime):
    """Default runtime: run commands directly on the host.

    The ``host`` runtime manages no environment — it is the "bring your own
    Odoo" runtime: it execs the resolved Odoo executable with the project
    environment applied. ``osh init --runtime=host --odoo-command`` pins the
    run command a system init file would use (e.g. ``/usr/bin/odoo``);
    without it Odoo resolves from ``.venv/bin``, ``.osh/odoo`` sources, or
    ``PATH``. ``--odoo-conf`` seeds the generated config from a base file.
    Managed targets such as ``venv`` subclass it and layer their
    environment on top.
    """

    name = "host"
    label = "Host"
    runtime_type = "runtime"
    host_executable = True
    description = "Run Odoo directly on the host (default)."
    help_text = (
        "Runs commands directly on the host with the project's Odoo config "
        "and database environment applied — no virtualenv or container is "
        "managed; use it on servers where the environment already exists. Odoo itself is resolved from ``.venv/bin``, ``.osh/odoo`` "
        "sources, or ``PATH``."
    )

    @classmethod
    def get_init_options(cls):
        """``--odoo-command``/``--odoo-conf`` for a bring-your-own Odoo."""
        return [
            click.Option(
                ["--odoo-command"],
                metavar="CMD",
                help="Odoo run command to use, as a system init file would "
                "run it (e.g. '/usr/bin/odoo' or 'odoo-bin').",
            ),
            click.Option(
                ["--odoo-conf"],
                metavar="FILE",
                type=click.Path(),
                help="Base Odoo config file to seed the generated config "
                "from (e.g. '/etc/odoo/odoo.conf'); the project's own "
                "config still overrides it.",
            ),
        ]

    def odoo_command(self, base):
        """Return the ``--odoo-command`` recorded at init as an argv list."""
        command = get_project_config(base, "init", "odoo_command")
        return shlex.split(command) if command else None

    def base_odoo_conf(self, base):
        """Return the ``--odoo-conf`` recorded at init, or None.

        Relative paths resolve inside the project directory.
        """
        conf = get_project_config(base, "init", "odoo_conf")
        if not conf:
            return None
        path = Path(conf).expanduser()
        return path if path.is_absolute() else Path(base) / path

    def _configured_exe(self, base):
        """Return the configured command's executable token, or None."""
        command = self.odoo_command(base)
        return command[0] if command else None

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
        exe = self._configured_exe(base) or find_odoo_executable(base)
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
            configured = self.odoo_command(base)
            exe = configured[0] if configured else find_odoo_executable(base)
            if "odoo_executable" in sections and exe:
                d.add_info("odoo_executable", str(exe))
                if configured:
                    d.add_info("odoo_command", " ".join(configured))
                    if phase != "init" and not (
                        Path(exe).is_file() or shutil.which(exe)
                    ):
                        d.add_warning(
                            f"Configured Odoo executable '{exe}' "
                            "does not resolve on PATH or disk."
                        )
            if phase == "init":
                self._diagnose_byo_options(d, base, options)

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
            if base_conf := self.base_odoo_conf(base):
                if base_conf.is_file():
                    d.add_info("odoo_base_conf", str(base_conf))
                else:
                    d.add_warning(
                        f"Configured base Odoo config '{base_conf}' " "does not exist."
                    )
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

    def _diagnose_byo_options(self, d, base, options):
        """Validate the ``--odoo-command``/``--odoo-conf`` init options."""
        if command := options.get("odoo_command"):
            tokens = shlex.split(command)
            exe = tokens[0] if tokens else ""
            if not exe or not (Path(exe).is_file() or shutil.which(exe)):
                d.add_error(
                    f"Odoo command '{command}' resolves to " "nothing on PATH or disk."
                )
            elif "-c" in tokens or any(
                token.startswith("--config") for token in tokens
            ):
                d.add_warning(
                    "The '-c'/'--config' option inside the Odoo "
                    "command overrides the config Osh generates — "
                    "pass the base file via --odoo-conf instead."
                )
        if conf_option := options.get("odoo_conf"):
            conf_path = Path(conf_option).expanduser()
            if not conf_path.is_absolute():
                conf_path = Path(base) / conf_path
            if not conf_path.is_file():
                d.add_error(f"Odoo config file '{conf_option}' does not exist.")

    def _add_init_plans(self, todo):
        """The ``host`` runtime manages no environment — nothing to install."""
        todo.add_plan(
            "Nothing to install: the 'host' runtime runs commands on the host"
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
        """Register the project; the ``host`` runtime manages no environment."""
        return True

    def _base_env(self, base, capture):
        """Return environment layered on top of the host env (none here).

        Managed runtimes (e.g. ``venv``) override this to inject their
        environment. *capture* marks internal subprocess calls so runtimes
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
            configured = Path(self._configured_exe(base) or "").name
            if odoo_exe in ("odoo-bin", "odoo", configured):
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

    def _odoo_process_names(self, base):
        """Extra executable names ``_looks_like_odoo`` should accept.

        A configured ``--odoo-command`` may run a binary that does not
        carry an Odoo-looking name (a wrapper such as ``odoo-server``);
        without it ``osh stop`` would refuse to kill its own process.
        """
        command = self.odoo_command(base)
        return [Path(command[0]).name] if command else []

    def odoo_port(self, base):
        """HTTP port from the project configs, then the configured base conf."""
        extra = [conf] if (conf := self.base_odoo_conf(base)) else []
        return get_odoo_port(base, extra_confs=extra)

    def status(self, ctx, base):
        """Report the process listening on the project's HTTP port, if any."""
        if command := self.odoo_command(base):
            echo.info(f"Odoo command: {' '.join(command)}")
        port = self.odoo_port(base)
        listeners = _port_listeners(port)
        if not listeners:
            echo.info(f"No process listening on port {port}.")
            return
        for pid in listeners:
            echo.info(
                f"Listening on port {port}: {_pid_command(pid) or 'unknown'} "
                f"(pid {pid})"
            )

    def port_holders(self, ctx, port, base):
        """Report host processes listening on *port* and their owners."""
        lines = []
        for pid in _port_listeners(port):
            cmdline = _pid_command(pid) or "unknown"
            desc = f"'{cmdline}' (pid {pid})"
            project = _pid_osh_project(pid)
            if project is not None:
                if Path(project).resolve() == Path(base).resolve():
                    desc += " — this project's Odoo process"
                else:
                    desc += (
                        f" — Osh project {project}; "
                        f"run 'osh stop {project}' to free it"
                    )
            else:
                cwd = _pid_cwd(pid)
                if cwd:
                    desc += f" in {cwd}"
                desc += (
                    " — Odoo process not managed by Osh"
                    if _looks_like_odoo(cmdline)
                    else " — not an Odoo process"
                )
            lines.append(desc)
        return lines

    def stop_all(self, ctx, *, dry_run=False, **options):
        """Stop every Osh-managed Odoo process found on this host.

        Osh exports ``ODOO_RC=<project>/.osh/odoo.conf`` into every command
        it runs, so an Odoo-looking process whose environment points at a
        ``.osh`` config is unambiguously Osh-managed. Discovery reads
        ``/proc`` and silently does nothing where it does not exist.
        """
        targets = _osh_managed_odoo_pids()
        if not targets:
            echo.info("No Osh-managed Odoo processes found.", err=True)
            return
        if dry_run:
            for pid, cmdline, _extra_names in targets:
                project = _pid_osh_project(pid)
                location = f" — {project}" if project else ""
                echo.info(f"Would stop Odoo process {pid} ({cmdline}){location}.")
            return
        pending = []
        for pid, cmdline, extra_names in targets:
            echo.info(f"Stopping Odoo process {pid} ({cmdline})...", err=True)
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError as exc:
                echo.warning(f"Could not stop pid {pid}: {exc}")
                continue
            pending.append((pid, extra_names))
        deadline = time.monotonic() + _SIGTERM_GRACE_SECONDS
        pending = [(p, n) for p, n in pending if _pid_running(p)]
        while pending and time.monotonic() < deadline:
            time.sleep(_PORT_POLL_INTERVAL_SECONDS)
            pending = [(p, n) for p, n in pending if _pid_running(p)]
        for pid, extra_names in pending:
            # Re-check identity before escalating: a process that exited may
            # have had its pid reused for something unrelated.
            if not _looks_like_odoo(_pid_command(pid), extra_names=extra_names):
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

    def stop(self, ctx, base, *, dry_run=False, **options):
        """Stop an Odoo process left listening on the project's HTTP port."""
        port = self.odoo_port(base)
        extra_names = self._odoo_process_names(base)
        listeners = _port_listeners(port)
        if not listeners:
            echo.info(f"Nothing to stop: no process listening on port {port}.")
            return
        for pid in listeners:
            cmdline = _pid_command(pid)
            if not _looks_like_odoo(cmdline, extra_names=extra_names):
                echo.warning(
                    f"Port {port} is held by '{cmdline or 'unknown'}' "
                    f"(pid {pid}), which does not look like Odoo — "
                    + (
                        "'osh stop' would leave it alone."
                        if dry_run
                        else "leaving it alone."
                    )
                )
                continue
            if dry_run:
                cwd = _pid_cwd(pid)
                location = f" in {cwd}" if cwd else ""
                echo.info(f"Would stop Odoo process {pid} ({cmdline}){location}.")
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
            if not _looks_like_odoo(_pid_command(pid), extra_names=extra_names):
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


# Deprecated aliases kept for plugins written against the old backend API.
Backend = Runtime
HostBackend = HostRuntime
NoneBackend = HostRuntime


def _osh_managed_odoo_pids():
    """Return ``(pid, cmdline, extra_names)`` of Odoo processes run under Osh.

    Reads ``/proc`` (Linux only): a process qualifies when its environment
    carries ``ODOO_RC`` pointing inside a ``.osh`` directory — which ``osh
    odoo``/``osh shell`` export — and its command line passes
    :func:`_looks_like_odoo`. *extra_names* is the basename of the
    project's configured ``--odoo-command``, so bring-your-own binaries
    (``odoo-server``, wrappers) are found too. Returns ``[]`` where
    ``/proc`` is unavailable.
    """
    proc = Path("/proc")
    if not proc.is_dir():
        return []
    found = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        project = _pid_osh_project(int(entry.name))
        if project is None:
            continue
        extra_names = _configured_command_names(project)
        cmdline = _pid_command(int(entry.name))
        if _looks_like_odoo(cmdline, extra_names=extra_names):
            found.append((int(entry.name), cmdline, extra_names))
    return found


def _pid_osh_project(pid):
    """Return the Osh project an Osh-run *pid* belongs to, or None.

    ``osh odoo``/``osh shell`` export ``ODOO_RC=<project>/.osh/odoo.conf``,
    so a process whose environment carries an ``ODOO_RC`` inside ``.osh``
    maps back to its project.
    """
    try:
        environ = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return None
    for var in environ.split(b"\0"):
        if not var.startswith(b"ODOO_RC="):
            continue
        parts = Path(var[len(b"ODOO_RC=") :].decode(errors="replace")).parts
        if ".osh" in parts:
            return Path(*parts[: parts.index(".osh")])
    return None


def _configured_command_names(project):
    """Return the ``--odoo-command`` executable name recorded by *project*.

    The project's ``init.odoo_command`` may name a binary (e.g.
    ``odoo-server``) that does not look like Odoo by name.
    """
    command = get_project_config(project, "init", "odoo_command")
    tokens = shlex.split(command) if command else []
    return (Path(tokens[0]).name,) if tokens else ()


def _pid_running(pid):
    """Return True while *pid* is alive and not a zombie."""
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        return bool(Path(f"/proc/{pid}/cmdline").read_bytes())
    except OSError:
        # No readable /proc entry: the kill probe answered already.
        return True


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
        return sorted({int(p) for p in out.split() if p.strip().isdigit()})

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
        return sorted({int(p) for p in (out or "").split() if p.isdigit()})

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
    return sorted(set(pids))


def _pid_cwd(pid):
    """Return *pid*'s working directory as a string, or None."""
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return None


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


def _looks_like_odoo(cmdline, extra_names=()):
    """Return True when *cmdline* invokes Odoo.

    Matches a direct ``odoo``/``odoo-bin``/``odoo.py``/``odoo.sh``
    executable as well as ``python -m odoo``, where ``odoo`` is a module
    argument rather than the executable; *extra_names* accepts additional
    executable names (e.g. the basename of a configured run command).
    Deliberately conservative: a false negative only leaves a process
    running, while a false positive would kill something that merely
    happens to hold the port.
    """
    try:
        tokens = shlex.split(cmdline or "")
    except ValueError:
        tokens = (cmdline or "").split()
    for index, token in enumerate(tokens):
        name = Path(token).name
        if name in _ODOO_EXECUTABLES + tuple(extra_names):
            return True
        if name in _PYTHON_EXECUTABLES and tokens[index + 1 : index + 3] == [
            "-m",
            "odoo",
        ]:
            return True
    return False


def _env_fingerprints_path(base):
    """Return the recorded environment input fingerprints file for *base*."""
    return Path(base) / ".osh" / "cache" / "env-fingerprints.json"


def _load_env_fingerprints(base):
    """Load the ``{runtime: {artifact: {label: digest}}}`` fingerprint store."""
    try:
        return json.loads(_env_fingerprints_path(base).read_text())
    except (OSError, ValueError):
        return {}


def _fingerprint_inputs(base, inputs):
    """Map each *inputs* entry to ``label -> sha256`` — None when unreadable.

    Labels are paths relative to *base* where possible so a moved project
    still points at the same files; the ``spec`` value — e.g. a resolved
    Compose ``build:`` mapping — is hashed under a pseudo-label that cannot
    collide with a real path.
    """
    fp = {}
    resolved_base = Path(base).resolve()
    for path in inputs.paths:
        path = Path(path)
        try:
            label = path.resolve().relative_to(resolved_base).as_posix()
        except (OSError, ValueError):
            label = str(path)
        try:
            fp[label] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            fp[label] = None
    if inputs.spec is not None:
        fp["<build definition>"] = hashlib.sha256(
            json.dumps(inputs.spec, sort_keys=True, default=str).encode()
        ).hexdigest()
    return fp
