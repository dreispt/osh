"""Docker Compose backend implementation for ``osh init`` and ``osh odoo``."""

import hashlib
import os
import re
import shlex
import sys
from pathlib import Path

import click

from ... import echo
from ...backends import Backend, copy_odoo_rc_to_osh_conf
from ...commands.helpers import Diagnostics
from ...common import run_command, run_subprocess
from ...sources import ensure_osh_sources
from .utils import (
    _COMPOSE_FILE,
    _DOCKER_TOML,
    _SOURCES_COMPOSE_FILE,
    _compose_base_command,
    _container_running_status,
    _find_compose_tool,
    _find_port_holder,
    _generate_compose_file,
    _load_docker_config,
    _port_in_use,
    _run_smoke_test,
    _save_docker_config,
)


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

    def _write_source_mounts_override(self, base, service):
        """Write the Compose override mounting out-of-project addon sources.

        Returns True when the file was created, changed or removed. Sources
        resolving outside the project root are mounted read-only under
        ``/mnt/osh-src``; the file is removed when none are needed.
        """
        override = Path(base) / _SOURCES_COMPOSE_FILE
        mounts = [
            f"      - {host}:{container}:ro"
            for host, container in self._source_mounts(base, include_themes=True)
            if not container.startswith("/mnt/extra-addons/")
        ]
        if not mounts:
            if override.exists():
                override.unlink()
                return True
            return False
        content = (
            "# Generated by osh — read-only mounts for addon sources outside\n"
            "# the project directory. Do not edit; regenerated on each run.\n"
            "services:\n"
            f"  {service}:\n"
            "    volumes:\n" + "\n".join(mounts) + "\n"
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

    def ensure_service_up(self, base, *, compose_file=None):
        """Start the project's Compose stack unless it is already running.

        Idempotent and cheap once the stack is up: a ``compose ps`` probe
        short-circuits before ``compose up -d``. The configured host port is
        checked first so a collision produces an actionable error instead of
        a raw Compose failure.
        """
        cfg = _load_docker_config(base) or {}
        service = cfg.get("service") or "odoo"
        # Regenerate the external-source mounts override before building the
        # Compose command so its -f is picked up; a changed override forces
        # `up -d` so a running stack picks up the new mounts.
        mounts_changed = self._write_source_mounts_override(base, service)
        compose_cmd = _compose_base_command(base, compose_file=compose_file)

        if _service_running(compose_cmd, service, base) and not mounts_changed:
            return

        self._check_port_available(base, cfg)
        docker_args = [*compose_cmd, "up", "-d"]
        echo.info(f"Running: {shlex.join(docker_args)}", err=True)
        run_command(docker_args, cwd=base, check=True, stream=True)

    def _check_port_available(self, base, cfg):
        """Raise an actionable error when the configured host port is taken."""
        try:
            port = int(cfg.get("port") or 8069)
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
                "there, or 'osh docker init --port <n>' here."
            )
        raise click.ClickException(
            f"Port {port} is already in use. If this is from a previous "
            "'osh odoo'/'osh shell' session, run 'osh docker stop' in that project "
            f"to free it. Otherwise, stop whatever's using port {port}, or "
            "run 'osh docker init --port <n>' here."
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
        try:
            compose_cmd = _compose_base_command(base, compose_file=compose_file)
        except click.ClickException as exc:
            echo.warning(exc.format_message())
            return
        docker_args = [*compose_cmd, "down"]
        echo.info(f"Running: {shlex.join(docker_args)}", err=True)
        run_command(docker_args, cwd=base, check=True, stream=True)

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

        cfg = _load_docker_config(base)
        service = cfg.get("service")
        if not service:
            raise click.ClickException(
                "No Docker service configured. Run "
                "'osh docker init --service <name>' or edit "
                f"{base / _DOCKER_TOML}."
            )

        cli_params = getattr(ctx, "params", {}) or {}
        compose_file = cli_params.get("compose_file")
        compose_cmd = _compose_base_command(base, compose_file=compose_file)

        docker_args = self._exec_args(
            base, compose_cmd, service, cfg, env_spec, capture
        )

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

        self.ensure_service_up(base, compose_file=compose_file)

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


def _service_running(compose_cmd, service, base):
    """Return True when *service* has a running container in this project."""
    returncode, out, _ = run_subprocess(
        [*compose_cmd, "ps", "--status", "running", "-q", service],
        cwd=base,
    )
    return returncode == 0 and bool(out.strip())


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
