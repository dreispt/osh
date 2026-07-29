"""Docker Compose backend implementation for ``osh init`` and ``osh odoo``."""

import os
from pathlib import Path

import click

from ... import echo
from ...backends import Backend, EnvSpec, copy_odoo_rc_to_osh_conf
from ...commands.helpers import Diagnostics
from ...common import run_command
from ...sources import ensure_osh_sources
from .utils import (
    _COMPOSE_FILE,
    _DOCKER_TOML,
    _compose_base_command,
    _find_compose_tool,
    _generate_compose_file,
    _load_docker_config,
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
        opts = [
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
        ]
        for o in opts:
            o.target_group = cls.name
        return opts

    _DIAGNOSE_SECTIONS = (
        "compose_tool",
        "config",
        "compose_file",
        "odoo_version",
        "service",
        "sources",
    )

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
            d.add_error(
                "Docker backend config not found. "
                "Run 'osh init --target docker' first."
            )
        else:
            d.add_warning(
                "Docker backend config not found. Run 'osh init --target docker'."
            )

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
        todo.add_plan("Generate .osh/docker-compose.yml")
        todo.add_plan("Write .osh/docker.toml with service and compose tool")
        todo.add_plan("Ensure Odoo sources for the selected edition")
        todo.add_plan("Run an Odoo --version smoke test")

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

        if compose_file and not (target / compose_file).is_file():
            raise click.ClickException(
                f"Compose file '{compose_file}' not found in {target}."
            )

        if not compose_file:
            if not dry_run:
                todo.start()
            _generate_compose_file(target, version, dry_run=dry_run)
            compose_file = str(_COMPOSE_FILE)

        copy_odoo_rc_to_osh_conf(target)

        if dry_run:
            _save_docker_config(
                target,
                service,
                command,
                compose_file,
                version=version,
                edition=edition,
                dry_run=True,
            )
            ensure_osh_sources(
                target,
                version,
                edition,
                dry_run=True,
                skip_odoo=True,
                assume_yes=options.get("assume_yes", False),
                enterprise_source=options.get("enterprise_source"),
                themes_source=options.get("themes_source"),
            )
            return True

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
        )

        todo.start()
        ensure_osh_sources(
            target,
            version,
            edition,
            dry_run=False,
            skip_odoo=True,
            assume_yes=options.get("assume_yes", False),
            enterprise_source=options.get("enterprise_source"),
            themes_source=options.get("themes_source"),
        )

        todo.start()
        _run_smoke_test(target, compose_file=compose_file)

        return True

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

        if not isinstance(env_spec, EnvSpec):
            env_spec = EnvSpec(argv=list(env_spec))

        cfg = _load_docker_config(base)
        service = cfg.get("service")
        if not service:
            raise click.ClickException(
                "No Docker service configured. Run "
                "'osh init --target docker --service <name>' or edit "
                f"{base / _DOCKER_TOML}."
            )

        cli_params = getattr(ctx, "params", {}) or {}
        compose_cmd = _compose_base_command(
            base, compose_file=cli_params.get("compose_file")
        )

        args = list(env_spec.argv)
        if args and args[0] == "odoo":
            command = _cfg_value(cfg, "command")
            if command:
                args = command.split() + args[1:]

        if not args:
            shells = ["bash", "sh"]
        else:
            shells = [args[0]]

        env = dict(env_spec.env)
        if "ODOO_RC" in env:
            host_path = Path(env["ODOO_RC"])
            container_path = str(host_path).replace(str(base), "/mnt/extra-addons")
            env["ODOO_RC"] = container_path

        base_docker_args = [*compose_cmd, "run", "--rm", "--service-ports"]
        for key, value in env.items():
            base_docker_args.extend(["-e", f"{key}={value}"])
        base_docker_args.append(service)

        for shell in shells:
            docker_args = [*base_docker_args, shell, *args[1:]]
            if dry_run:
                echo.info(f"Would run: {' '.join(docker_args)}", err=True)
                return

            echo.info(f"Running: {' '.join(docker_args)}", err=True)

            if wait:
                run_command(docker_args, check=True, stream=True)
                return

            try:
                os.execvp(docker_args[0], docker_args)
            except FileNotFoundError:
                if shell != shells[-1]:
                    continue
                raise click.ClickException(
                    f"Could not run docker: {docker_args[0]} not found"
                )
            except OSError as exc:  # pragma: no cover
                raise click.ClickException(f"Could not run docker: {exc}") from exc


def _cfg_value(cfg, key, default=None):
    """Return *key* from *cfg* when available, otherwise *default*."""
    return cfg.get(key, default) if cfg else default
