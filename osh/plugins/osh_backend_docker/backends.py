"""Docker Compose backend implementation for ``osh init`` and ``osh odoo``."""

import hashlib
import os
import re
import shlex
import sys
import time
from pathlib import Path

import click

from ... import echo
from ...backends import Backend, copy_odoo_rc_to_osh_conf
from ...commands.helpers import Diagnostics
from ...common import odoo_http_port, run_command, run_subprocess
from ...sources import ensure_osh_sources
from .utils import (
    _COMPOSE_FILE,
    _DOCKER_TOML,
    _SOURCES_COMPOSE_FILE,
    _compose_base_command,
    _compose_project_names_for,
    _container_running_status,
    _find_compose_tool,
    _find_port_holder,
    _find_project_stack,
    _generate_compose_file,
    _load_docker_config,
    _port_in_use,
    _port_process_hint,
    _run_smoke_test,
    _save_docker_config,
)

# Sentinel for ``_exec_env``'s *port*: keep the port the running stack or a
# pending 'osh odoo -p' invocation requests, instead of resetting to the
# configured one. Database probes and interactive shells keep; Odoo runs
# always resolve to an explicit port (or None for the configured default).
_PORT_KEEP = object()

# ``_wait_for_db_ready`` timings after ``up -d``: how long to poll the
# database service before continuing anyway, and how often to retry.
_DB_READY_TIMEOUT_SECONDS = 60.0
_DB_READY_POLL_SECONDS = 0.5


class DockerBackend(Backend):
    """Unified Docker Compose backend for ``osh init`` and ``osh odoo``."""

    name = "docker"
    label = "Docker Compose"
    backend_type = "backend"
    description = (
        "Run Odoo inside a Docker Compose stack; generates a compose file if missing."
    )
    help_text = (
        "Writes ``.osh/docker.toml`` with the service name, command, and optional "
        "compose file path. If no compose file exists, generates ``.osh/docker-compose.yml`` "
        "with a standard Odoo + PostgreSQL stack using the requested version as the "
        "image tag.\n\n"
        "Requires Docker and the Docker Compose plugin on PATH."
    )

    @classmethod
    def get_cli_group(cls):
        # The ``docker`` group carries extra commands beyond the generated
        # lifecycle ones (custom ``stop``, ``list``) — built in commands.py.
        from .commands import docker

        return docker

    @classmethod
    def get_init_options(cls):
        return [
            click.Option(
                ["--service"],
                help="Docker Compose service name for the Odoo container.",
            ),
            click.Option(
                ["--command"],
                help="Shell-quoted command to run inside the container "
                "(e.g. 'odoo' or 'python3 -m odoo').",
            ),
            click.Option(
                ["--compose-file"],
                help="Docker Compose file to use (e.g. devel.yaml for Doodba).",
            ),
            click.Option(
                ["--db-service"],
                help="Docker Compose service name for the database container "
                "(defaults to 'db').",
            ),
            click.Option(
                ["--port"],
                type=int,
                help="Host port to publish Odoo on (defaults to 8069).",
            ),
            click.Option(
                ["-e", "--enterprise-source"],
                help="Enterprise source: an existing local directory or a git URL. "
                "Defaults to the central cache (populated from GitHub).",
            ),
            click.Option(
                ["-d", "--themes-source"],
                help="Design-themes source: an existing local directory or a git URL. "
                "Defaults to the central cache (populated from GitHub).",
            ),
        ]

    _DIAGNOSE_SECTIONS = (
        "compose_tool",
        "config",
        "compose_file",
        "odoo_version",
        "service",
        "sources",
        "container",
    )

    def detect_odoo_version(self, base):
        """Return the Odoo version from sources or the compose image tag."""
        version = super().detect_odoo_version(base)
        if version:
            return version

        cfg = _load_docker_config(base)
        compose_file = (cfg or {}).get("compose_file") or str(_COMPOSE_FILE)
        compose_path = base / Path(compose_file)
        if not compose_path.is_file():
            return None

        text = compose_path.read_text()
        match = re.search(r"image:\s*(?:\S+/)?odoo:(\S+)", text)
        if not match:
            return None

        version_match = re.match(r"(\d+\.\d+)", match.group(1))
        if version_match:
            return f"odoo {version_match.group(1)}"
        return None

    def diagnose_sections_for_phase(self, phase):
        """Skip the expensive Odoo version check in ``init`` and ``run``."""
        if phase == "init":
            return ["compose_tool", "config", "compose_file", "service"]
        if phase == "run":
            return ["compose_tool", "config", "compose_file", "service", "sources"]
        return list(self._DIAGNOSE_SECTIONS)

    def diagnose(
        self,
        base,
        ctx=None,
        *,
        sections=None,
        **options,
    ):
        """Inspect Docker Compose environment and project configuration."""
        phase = options.get("phase", "doctor")
        d = Diagnostics(self.name, project=base)

        if sections is None:
            sections = self._DIAGNOSE_SECTIONS
        sections = set(sections)

        cfg = _load_docker_config(base)
        service = options.get("service") or _cfg_value(cfg, "service")
        command = options.get("command") or _cfg_value(cfg, "command")
        compose_file = options.get("compose_file") or _cfg_value(cfg, "compose_file")
        edition = (options.get("edition") or _cfg_value(cfg, "edition") or "ce").lower()
        version = options.get("version") or _cfg_value(cfg, "version") or ""

        if "compose_tool" in sections:
            self._diagnose_compose_tool(d, phase, cfg)
        if "config" in sections:
            self._diagnose_config(
                d, phase, cfg, service, command, compose_file, edition
            )
        if "compose_file" in sections:
            self._diagnose_compose_file(d, phase, base, compose_file)
        if "odoo_version" in sections:
            self._diagnose_odoo_version(d, phase, base)
        if "service" in sections:
            self._diagnose_service(d, phase, service)
        if "container" in sections and cfg:
            self._diagnose_container(d, base, service or "odoo")
        if (
            "sources" in sections
            and phase == "run"
            and edition in ("ee", "sh")
            and not version
        ):
            self._diagnose_sources(d, base, edition)

        return d

    def _diagnose_compose_tool(self, d, phase, cfg):
        """Detect and record the available Docker Compose tool."""
        cached_tool = _cfg_value(cfg, "compose_tool")
        # Use the cached tool during ``run`` for efficiency; init/doctor detect.
        if phase == "run" and cached_tool:
            compose_tool = cached_tool.split()
        else:
            compose_tool = _find_compose_tool()
        if compose_tool:
            d.add_info("compose_tool", " ".join(compose_tool), topic="System")
        else:
            d.add_error(
                "No Docker Compose tool found. "
                "Install 'docker compose' or 'docker-compose'."
            )

    def _diagnose_config(self, d, phase, cfg, service, command, compose_file, edition):
        """Report the saved Docker backend configuration."""
        if cfg:
            d.add_info("service", service or "odoo")
            d.add_info("command", command or "odoo-bin")
            d.add_info("compose_file", compose_file or "<none>")
            d.add_info("edition", edition)
            if cfg.get("db_service"):
                d.add_info("db_service", cfg["db_service"])
            if cfg.get("compose_tool"):
                d.add_info("configured_compose_tool", cfg["compose_tool"])
        elif phase == "init":
            d.add_warning(
                "Docker backend config not found; it will be created during init."
            )
        elif phase == "run":
            d.add_error("Docker backend config not found. Run 'osh docker init' first.")
        else:
            d.add_warning("Docker backend config not found. Run 'osh docker init'.")

    def _diagnose_compose_file(self, d, phase, base, compose_file):
        """Check the resolved Docker Compose file."""
        compose_path = (
            base / Path(compose_file) if compose_file else base / _COMPOSE_FILE
        )
        if compose_path.exists():
            d.add_info("generated_compose_file", str(compose_path))
        elif phase == "init":
            if compose_file:
                d.add_error(f"Compose file not found: {compose_path}")
            else:
                d.add_plan(f"Generate {compose_path}")
        elif phase == "run":
            d.add_error(f"Compose file not found: {compose_path}")
        else:
            d.add_warning(f"Compose file not found: {compose_path}")

    def _diagnose_odoo_version(self, d, phase, base):
        """Detect and record the installed Odoo version."""
        odoo_version = self.detect_odoo_version(base)
        if odoo_version:
            d.add_info("odoo_version", odoo_version)
        elif phase == "doctor":
            d.add_warning("Could not determine installed Odoo version.")

    def _diagnose_service(self, d, phase, service):
        """Validate the configured Docker Compose service."""
        if not service:
            if phase == "init":
                d.add_warning("No --service provided; defaulting to 'odoo'.")
            elif phase == "run":
                d.add_error("No Docker service configured.")

    def _diagnose_container(self, d, base, service):
        """Report whether the project's service container is running."""
        try:
            compose_cmd = _compose_base_command(base)
        except click.ClickException:
            return
        running, uptime = _container_running_status(base, compose_cmd, service)
        if running is None:
            return
        if running:
            detail = f"running, started {uptime} ago" if uptime else "running"
            d.add_info("container", detail)
        else:
            d.add_info("container", "not running")

    def _diagnose_sources(self, d, base, edition):
        """Check that required source copies are present for EE/SH editions."""
        required = ["enterprise"]
        if edition == "sh":
            required.append("design-themes")
        missing = [name for name in required if not (base / ".osh" / name).exists()]
        if missing:
            d.add_error(
                f"Project is missing required source copies: {', '.join(missing)}. "
                "Run 'osh init' first."
            )

    def _add_init_plans(self, todo):
        """Record planned init actions (without doing work)."""
        todo.add_plan("Write .osh/docker.toml with service and compose tool")
        todo.add_plan("Ensure Odoo sources for the selected edition")
        todo.add_plan("Run an Odoo --version smoke test")

    def odoo_data_dir(self, base):
        """Return the container's Odoo data dir (``data_dir`` in docker.toml).

        Defaults to ``/var/lib/odoo``, the volume the official Odoo image
        declares; it is usually a named volume, so it has no host path.
        """
        cfg = _load_docker_config(base) or {}
        return cfg.get("data_dir") or "/var/lib/odoo"

    def build_addons_paths(self, base, *, include_themes=False):
        """Return a list of addon paths for *base* translated to container paths.

        Host paths under the project translate to the project mount under
        ``/mnt/extra-addons``. Paths resolving outside the project — e.g.
        an ``.osh/odoo`` link to a shared checkout — get a dedicated mount
        under ``/mnt/osh-src``, provided by the generated Compose override
        (see ``ensure_service_up``).
        """
        mounts = self._source_mounts(base, include_themes=include_themes)
        return [container for _, container in mounts]

    def _source_mounts(self, base, *, include_themes=False):
        """Return ``(host_path, container_path)`` pairs for addon sources.

        Symlinks are resolved on the host first — a path reached through a
        link must map to the real directory, which is what a container
        mount exposes. Sources outside the project root mount read-only
        under ``/mnt/osh-src/<name>-<digest>``.
        """
        resolved_base = Path(base).resolve()
        mounts = {}
        for path in super().build_addons_paths(base, include_themes=include_themes):
            resolved = path.resolve()
            try:
                rel = resolved.relative_to(resolved_base)
                container = f"/mnt/extra-addons/{rel}"
            except (ValueError, OSError):
                digest = hashlib.sha256(str(resolved).encode()).hexdigest()[:6]
                container = f"/mnt/osh-src/{resolved.name}-{digest}"
            mounts.setdefault(resolved, container)
        return list(mounts.items())

    def _write_compose_override(self, base, service, port=None):
        """Write the Compose override for extra mounts and a ``-p`` port.

        Returns True when the file was created, changed or removed. Sources
        resolving outside the project root are mounted read-only under
        ``/mnt/osh-src``; an explicit Odoo ``-p``/``--http-port`` republishes
        the service on that host port — ``!override`` replaces the base
        mapping, which would otherwise also bind the configured port. The
        file is removed when nothing needs overriding.
        """
        override = Path(base) / _SOURCES_COMPOSE_FILE
        mounts = [
            f"      - {host}:{container}:ro"
            for host, container in self._source_mounts(base, include_themes=True)
            if not container.startswith("/mnt/extra-addons/")
        ]
        sections = []
        if port:
            sections.append(f'    ports: !override\n      - "{port}:{port}"')
        if mounts:
            sections.append("    volumes:\n" + "\n".join(mounts))
        if not sections:
            if override.exists():
                override.unlink()
                return True
            return False
        content = (
            "# Generated by osh — overrides for the project's Compose stack.\n"
            "# Do not edit; regenerated on each run.\n"
            "services:\n"
            f"  {service}:\n" + "\n".join(sections) + "\n"
        )
        if override.exists() and override.read_text() == content:
            return False
        override.parent.mkdir(parents=True, exist_ok=True)
        override.write_text(content)
        return True

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
        """Set up the project to run Odoo with Docker Compose."""
        service = options.get("service")
        command = options.get("command")
        compose_file = options.get("compose_file")
        port = options.get("port")
        db_service = options.get("db_service")

        if compose_file and not (target / compose_file).is_file():
            raise click.ClickException(
                f"Compose file '{compose_file}' not found in {target}."
            )

        if not compose_file:
            if not dry_run:
                todo.start()
            _generate_compose_file(target, version, port=port or 8069, dry_run=dry_run)
            compose_file = str(_COMPOSE_FILE)

        if dry_run:
            _save_docker_config(
                target,
                service,
                command,
                compose_file,
                version=version,
                edition=edition,
                port=port,
                db_service=db_service,
                dry_run=True,
            )
            ensure_osh_sources(
                target,
                version,
                edition,
                dry_run=True,
                skip_odoo=True,
                assume_yes=options.get("assume_yes", False),
                confirmed=options.get("confirmed", False),
                enterprise_source=options.get("enterprise_source"),
                themes_source=options.get("themes_source"),
            )
            return True

        copy_odoo_rc_to_osh_conf(target)

        compose_tool = _find_compose_tool()
        if compose_tool is None:
            raise click.ClickException(
                "No Docker Compose tool found. "
                "Install 'docker compose' or 'docker-compose'."
            )

        todo.start()
        _save_docker_config(
            target,
            service,
            command,
            compose_file,
            version=version,
            edition=edition,
            compose_tool=" ".join(compose_tool),
            port=port,
            db_service=db_service,
        )

        todo.start()
        ensure_osh_sources(
            target,
            version,
            edition,
            dry_run=False,
            skip_odoo=True,
            assume_yes=options.get("assume_yes", False),
            confirmed=options.get("confirmed", False),
            enterprise_source=options.get("enterprise_source"),
            themes_source=options.get("themes_source"),
        )

        todo.start()
        _run_smoke_test(target, compose_file=compose_file)

        return True

    def ensure_service_up(self, base, *, compose_file=None, port=None):
        """Start the project's Compose stack unless it is already running.

        Idempotent and cheap once the stack is up: a ``compose ps`` probe
        short-circuits before ``compose up -d``. The published host port —
        *port* when ``osh odoo -p`` overrode it, else the configured one —
        is checked first so a collision produces an actionable error
        instead of a raw Compose failure.
        """
        cfg = _load_docker_config(base) or {}
        service = cfg.get("service") or "odoo"
        if port is not None:
            try:
                if port == int(cfg.get("port") or 8069):
                    port = None
            except (TypeError, ValueError):
                pass
        # Regenerate the override (external-source mounts, -p port) before
        # building the Compose command so its -f is picked up; a changed
        # override forces `up -d` so a running stack picks up the changes.
        override_changed = self._write_compose_override(base, service, port=port)
        compose_cmd = _compose_base_command(base, compose_file=compose_file)

        if _service_running(compose_cmd, service, base) and not override_changed:
            return

        self._check_port_available(base, cfg, port=port)
        docker_args = [*compose_cmd, "up", "-d"]
        echo.info(f"Running: {shlex.join(docker_args)}", err=True)
        run_command(docker_args, cwd=base, check=True, stream=True)
        _wait_for_db_ready(compose_cmd, cfg.get("db_service") or "db", base)

    def _check_port_available(self, base, cfg, port=None):
        """Raise an actionable error when the effective host port is taken."""
        try:
            port = int(port or cfg.get("port") or 8069)
        except (TypeError, ValueError):
            port = 8069
        if not _port_in_use(port):
            return
        holder = _find_port_holder(port)
        if holder and Path(holder[0]) != Path(base):
            project_path, running_for = holder
            raise click.ClickException(
                f"Port {port} is already used by a container for "
                f"{project_path} (running {running_for}). Run 'osh docker stop' "
                "there, or 'osh odoo -p <n>' here."
            )
        process = _port_process_hint(port)
        if process:
            raise click.ClickException(
                f"Port {port} is already used by {process}. "
                "If it's an Osh session, run 'osh backend stop' there; "
                "otherwise stop it, or run 'osh odoo -p <n>' here."
            )
        raise click.ClickException(
            f"Port {port} is already in use. If this is from a previous "
            "'osh odoo'/'osh shell' session, run 'osh docker stop' in that project "
            f"to free it. Otherwise, stop whatever's using port {port}, or "
            "run 'osh odoo -p <n>' here."
        )

    @classmethod
    def get_stop_options(cls):
        return [
            click.Option(
                ["--compose-file"],
                default=None,
                envvar="OSH_COMPOSE_FILE",
                help="Use this compose file instead of the configured or "
                "generated one.",
            ),
        ]

    def stop(self, ctx, base, **options):
        """Stop and remove this project's Compose stack."""
        cfg = _load_docker_config(base)
        if not cfg:
            echo.info("No Docker backend configured; nothing to stop.", err=True)
            return
        compose_file = (
            options.get("compose_file") or cfg.get("compose_file") or _COMPOSE_FILE
        )
        compose_path = Path(compose_file)
        if not compose_path.is_absolute():
            compose_path = Path(base) / compose_path
        if not compose_path.exists():
            echo.info(
                f"Nothing to stop: compose file {compose_file} does not exist.",
                err=True,
            )
            return
        # Down every Compose project this project's containers run under —
        # a stack started outside ``osh odoo`` may carry a different project
        # name than the derived one, and the two can coexist.
        try:
            names = _compose_project_names_for(base)
        except click.ClickException:
            names = set()
        for project_name in sorted(names) or [None]:
            try:
                compose_cmd = _compose_base_command(
                    base, compose_file=compose_file, project_name=project_name
                )
            except click.ClickException as exc:
                echo.warning(exc.format_message())
                continue
            docker_args = [*compose_cmd, "down"]
            echo.info(f"Running: {shlex.join(docker_args)}", err=True)
            run_command(docker_args, cwd=base, check=True, stream=True)

    def stop_by_name(self, ctx, name, **options):
        """Stop the Compose stack of the Osh project named *name*.

        *name* is the project directory name shown by ``osh docker list``
        (a path or ``osh-*`` Compose project name also work). When the
        project directory is still present this is the same ``compose
        down`` as ``osh docker stop`` there; otherwise the stack is torn
        down from the containers' Compose labels.
        """
        stack = _find_project_stack(name)
        if stack["osh"]:
            self.stop(ctx, stack["path"], **options)
            return

        # The config_files label is comma-separated when the stack was
        # started with several -f options; keep the ones still on disk.
        compose_file = options.get("compose_file") or stack["config_file"] or ""
        compose_files = [p for p in compose_file.split(",") if Path(p).is_file()]
        if stack["compose_project"] and compose_files:
            compose_tool = _find_compose_tool()
            if compose_tool is None:
                raise click.ClickException(
                    "No Docker Compose tool found. "
                    "Install 'docker compose' or 'docker-compose'."
                )
            docker_args = [*compose_tool, "-p", stack["compose_project"]]
            for path in compose_files:
                docker_args.extend(["-f", path])
            docker_args.append("down")
        else:
            # The project directory is gone along with its compose file —
            # remove the leftover containers directly.
            docker_args = ["docker", "rm", "-f", *stack["ids"]]
        echo.info(f"Running: {shlex.join(docker_args)}", err=True)
        run_command(docker_args, check=True, stream=True)

    def env(
        self,
        ctx,
        base,
        env_spec,
        *,
        dry_run=False,
        **options,
    ):
        cfg = _load_docker_config(base)
        service = cfg.get("service")
        if not service:
            raise click.ClickException(
                "No Docker service configured. Run "
                "'osh docker init --service <name>' or edit "
                f"{base / _DOCKER_TOML}."
            )
        argv = env_spec.argv or []
        # Server run: ``odoo`` followed by flags only (or a flag argv when no
        # command is configured). Subcommands (``odoo shell``…) and non-Odoo
        # commands keep the running/pending port — they never bind HTTP.
        is_server_run = argv and (
            argv[0].startswith("-")
            or (argv[0] == "odoo" and (len(argv) == 1 or argv[1].startswith("-")))
        )
        port = odoo_http_port(argv) if is_server_run else _PORT_KEEP
        return self._exec_env(
            ctx,
            base,
            env_spec,
            cfg,
            service,
            self._exec_args,
            dry_run=dry_run,
            port=port,
            **options,
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
        """Run a command in the Compose database service instead of Odoo's.

        The ``db_service`` key in ``.osh/docker.toml`` names the service;
        ``db`` is the default, matching the generated stack and Doodba.
        """
        cfg = _load_docker_config(base)
        if not cfg.get("service"):
            raise click.ClickException(
                "No Docker service configured. Run "
                "'osh docker init --service <name>' or edit "
                f"{base / _DOCKER_TOML}."
            )
        return self._exec_env(
            ctx,
            base,
            env_spec,
            cfg,
            cfg.get("db_service") or "db",
            self._db_exec_args,
            dry_run=dry_run,
            **options,
        )

    def _exec_env(
        self,
        ctx,
        base,
        env_spec,
        cfg,
        service,
        exec_args,
        *,
        dry_run=False,
        port=_PORT_KEEP,
        **options,
    ):
        """Dispatch a ``compose exec`` call against *service*.

        *exec_args* is the argument builder (``_exec_args`` for the Odoo
        service, ``_db_exec_args`` for the database one); the rest of the
        flow — stack lifecycle, dry-run, capture/wait/exec — is shared.
        """
        wait = options.pop("wait", False)
        capture = options.pop("capture", False)
        if port is _PORT_KEEP:
            port = _requested_port(ctx, base)

        cli_params = getattr(ctx, "params", {}) or {}
        compose_file = cli_params.get("compose_file")
        compose_cmd = _compose_base_command(base, compose_file=compose_file)

        docker_args = exec_args(base, compose_cmd, service, cfg, env_spec, capture)

        if dry_run:
            if capture:
                # Read-only probe: answer only when the stack is already up,
                # without starting containers for a dry run.
                if not _service_running(compose_cmd, service, base):
                    return 1, "", ""
                return run_subprocess(
                    docker_args,
                    cwd=base,
                    input=env_spec.input,
                    stdin=env_spec.stdin,
                    stdout=options.get("stdout"),
                    text=options.get("text", True),
                )
            echo.info(f"Would run: {shlex.join(docker_args)}", err=True)
            return None

        self.ensure_service_up(base, compose_file=compose_file, port=port)

        if capture:
            # Internal calls (probes, db helpers) are not user commands —
            # keep them off the console.
            echo.debug(f"Running: {shlex.join(docker_args)}")
            return run_subprocess(
                docker_args,
                cwd=base,
                input=env_spec.input,
                stdin=env_spec.stdin,
                stdout=options.get("stdout"),
                text=options.get("text", True),
            )

        echo.info(f"Running: {shlex.join(docker_args)}", err=True)
        if wait:
            run_command(docker_args, cwd=base, check=True, stream=True)
            return None

        try:
            os.execvp(docker_args[0], docker_args)
        except FileNotFoundError:
            raise click.ClickException(
                f"Could not run docker: {docker_args[0]} not found"
            )
        except OSError as exc:  # pragma: no cover
            raise click.ClickException(f"Could not run docker: {exc}") from exc

    def _db_exec_args(self, base, compose_cmd, service, cfg, env_spec, capture):
        """Assemble the ``compose exec`` argv targeting the database service.

        Unlike ``_exec_args`` there is no Odoo command rewriting and no host
        path translation — the db container does not mount the project — and
        ``ODOO_RC`` is dropped for the same reason.
        """
        args = list(env_spec.argv)
        if not args:
            container_argv = ["sh", "-c", _DB_PG_ENV_SHELL_SCRIPT]
        else:
            container_argv = ["sh", "-c", _DB_PG_ENV_SCRIPT, "osh", *args]

        env = {**_CONTAINER_ENV_DEFAULTS, **env_spec.env}
        env.pop("ODOO_RC", None)

        docker_args = [*compose_cmd, "exec"]
        if (
            capture
            or env_spec.input is not None
            or env_spec.stdin is not None
            or not sys.stdin.isatty()
        ):
            docker_args.append("-T")
        for key, value in env.items():
            docker_args.extend(["-e", f"{key}={value}"])
        docker_args.append(service)
        docker_args.extend(container_argv)
        return docker_args

    def _exec_args(self, base, compose_cmd, service, cfg, env_spec, capture):
        """Assemble the ``compose exec`` argument vector for *env_spec*."""
        args = list(env_spec.argv)
        command = _cfg_value(cfg, "command") or "odoo"
        if args and args[0] == "odoo":
            args = command.split() + args[1:]
        elif args and args[0].startswith("-"):
            # Entrypoint-style odoo flags get the configured command prepended,
            # since ``compose exec`` bypasses the image entrypoint.
            args = command.split() + args

        if not args:
            container_argv = ["sh", "-c", _PG_ENV_SHELL_SCRIPT]
        else:
            container_argv = ["sh", "-c", _PG_ENV_SCRIPT, "osh", *args]

        env = {**_CONTAINER_ENV_DEFAULTS, **env_spec.env}
        if "ODOO_RC" in env:
            host_path = Path(env["ODOO_RC"])
            env["ODOO_RC"] = str(host_path).replace(str(base), "/mnt/extra-addons")

        docker_args = [*compose_cmd, "exec"]
        if (
            capture
            or env_spec.input is not None
            or env_spec.stdin is not None
            or not sys.stdin.isatty()
        ):
            docker_args.append("-T")
        for key, value in env.items():
            docker_args.extend(["-e", f"{key}={value}"])
        docker_args.append(service)
        docker_args.extend(_containerize_arg(a, base) for a in container_argv)
        return docker_args


def _requested_port(ctx, base):
    """Return the port this invocation should publish, or None for configured.

    Used for ``_PORT_KEEP`` calls (probes, ``osh shell``): a pending
    ``osh odoo -p`` stashes its value in ``ctx.obj['http_port']``; otherwise
    the port the compose override already publishes is kept so probes don't
    churn or strip it.
    """
    port = (getattr(ctx, "obj", None) or {}).get("http_port")
    if port:
        try:
            return int(port)
        except (TypeError, ValueError):
            pass
    override = Path(base) / _SOURCES_COMPOSE_FILE
    if override.is_file():
        match = re.search(r'^\s+- "?(\d+):\d+"?', override.read_text(), re.MULTILINE)
        if match:
            return int(match.group(1))
    return None


def _service_running(compose_cmd, service, base):
    """Return True when *service* has a running container in this project."""
    returncode, out, _ = run_subprocess(
        [*compose_cmd, "ps", "--status", "running", "-q", service],
        cwd=base,
    )
    return returncode == 0 and bool(out.strip())


def _wait_for_db_ready(compose_cmd, db_service, base):
    """Poll *db_service* until PostgreSQL accepts connections.

    ``compose up -d`` returns when containers start, not when PostgreSQL is
    ready; probes run right after hit "connection refused" and report
    existing databases as missing. Services absent from the Compose
    configuration are skipped, so stacks without a database service (or
    with an external one) are unaffected.
    """
    services = _compose_services(compose_cmd, base)
    if services is not None and db_service not in services:
        return
    if _db_ready(compose_cmd, db_service, base):
        return
    echo.info(
        f"Waiting for the '{db_service}' service to accept connections...",
        err=True,
    )
    deadline = time.monotonic() + _DB_READY_TIMEOUT_SECONDS
    while not _db_ready(compose_cmd, db_service, base):
        if time.monotonic() >= deadline:
            echo.warning(
                f"The '{db_service}' service is still not accepting "
                f"connections after {_DB_READY_TIMEOUT_SECONDS:.0f}s; "
                "continuing anyway."
            )
            return
        time.sleep(_DB_READY_POLL_SECONDS)


def _compose_services(compose_cmd, base):
    """Return the service names in the Compose configuration, or None."""
    returncode, out, _ = run_subprocess(
        [*compose_cmd, "config", "--services"], cwd=base
    )
    if returncode != 0:
        return None
    return set(out.split())


def _db_ready(compose_cmd, db_service, base):
    """Return True once *db_service* answers ``pg_isready``.

    A ``compose exec`` failure — the container is still starting — counts
    as not ready; an image without ``pg_isready`` counts as ready, since
    there is nothing to wait for.
    """
    returncode, _, _ = run_subprocess(
        [
            *compose_cmd,
            "exec",
            "-T",
            db_service,
            "sh",
            "-c",
            "command -v pg_isready > /dev/null 2>&1 || exit 0;"
            " exec pg_isready -q -h 127.0.0.1",
        ],
        cwd=base,
    )
    return returncode == 0


# Environment defaults applied to every ``compose exec``; ``env_spec.env``
# values take precedence. ``LC_ALL`` works around images like odoo:18 that
# set ``LANG=en_US.UTF-8`` without generating the locale, which makes
# perl-based tools (``dropdb``/``psql`` via pg_wrapper) warn on every run.
# ``C.UTF-8`` is always present in glibc-based images.
_CONTAINER_ENV_DEFAULTS = {"LC_ALL": "C.UTF-8"}

# Maps the Odoo image's database variables to the libpq ones, keeping any
# values already provided (e.g. ``-e PGHOST=...`` or a Compose environment).
# ``USER`` is the image's database user variable, as used by its entrypoint.
# ``compose exec`` bypasses that entrypoint, which would map them to ``--db_*``
# arguments; libpq environment variables reach both psycopg2 (Odoo itself)
# and tools like ``psql`` without argument rewriting.
_PG_ENV_EXPORTS = (
    'export PGHOST="${PGHOST:-$HOST}"'
    ' PGPORT="${PGPORT:-$PORT}"'
    ' PGUSER="${PGUSER:-$USER}"'
    ' PGPASSWORD="${PGPASSWORD:-$PASSWORD}";'
)

# Runs a command with the libpq variables exported.
_PG_ENV_SCRIPT = _PG_ENV_EXPORTS + ' exec "$@"'

# Interactive shell with the libpq variables exported, preferring bash.
_PG_ENV_SHELL_SCRIPT = (
    _PG_ENV_EXPORTS
    + " if command -v bash > /dev/null 2>&1; then exec bash; else exec sh; fi"
)

# Maps the Postgres image's own variables to the libpq ones, for commands
# run inside the ``db`` service by ``osh db shell``. Values already provided
# (``-e PGUSER=...``, from the project config) take precedence. ``PGHOST``
# is deliberately left unset so libpq uses the container's local socket.
_DB_PG_ENV_EXPORTS = (
    'export PGUSER="${PGUSER:-$POSTGRES_USER}"'
    ' PGPASSWORD="${PGPASSWORD:-$POSTGRES_PASSWORD}"'
    ' PGDATABASE="${PGDATABASE:-$POSTGRES_DB}";'
)

# Runs a command with the libpq variables exported.
_DB_PG_ENV_SCRIPT = _DB_PG_ENV_EXPORTS + ' exec "$@"'

# Interactive shell with the libpq variables exported, preferring bash.
_DB_PG_ENV_SHELL_SCRIPT = (
    _DB_PG_ENV_EXPORTS
    + " if command -v bash > /dev/null 2>&1; then exec bash; else exec sh; fi"
)


def _containerize_arg(arg, base):
    """Translate an absolute host path under *base* to its container mount."""
    value = str(arg)
    if "=" in value:
        key, _, path = value.partition("=")
        translated = _containerize_arg(path, base)
        if translated != path:
            return f"{key}={translated}"
        return value
    path = Path(value)
    if not path.is_absolute():
        return value
    try:
        rel = path.resolve().relative_to(Path(base).resolve())
    except (ValueError, OSError):
        return value
    return f"/mnt/extra-addons/{rel.as_posix()}"


def _cfg_value(cfg, key, default=None):
    """Return *key* from *cfg* when available, otherwise *default*."""
    return cfg.get(key, default) if cfg else default
