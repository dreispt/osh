"""Docker Compose backend implementation for ``osh init`` and ``osh run``."""

import os
from pathlib import Path

import click

from ...backends import Backend, RunSpec, copy_odoo_rc_to_osh_conf
from ...commons import resolve_config_file, run_command
from ...diagnostics import Diagnostics
from ...echo import friendly, get_echo, info, warning
from ...odoo_layout import build_addons_paths
from ...sources import ensure_osh_sources
from .utils import (
    _COMPOSE_FILE,
    _DOCKER_TOML,
    _compose_base_command,
    _docker_command,
    _find_compose_tool,
    _generate_compose_file,
    _load_docker_config,
    _save_docker_config,
)


class DockerBackend(Backend):
    """Unified Docker Compose backend for ``osh init`` and ``osh run``."""

    name = "docker"
    label = "Docker Compose"
    backend_type = "backend"
    neutralize_supported = True
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
                "(e.g. 'odoo-bin' or 'python3 -m odoo').",
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
        if phase == "init":
            self._add_init_plans(d)
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

    def _add_init_plans(self, d):
        """Record planned init actions."""
        d.add_plan("Write .osh/docker.toml with service and compose tool")
        d.add_plan("Ensure Odoo sources for the selected edition")
        d.add_plan("Run an Odoo --version smoke test")

    def init(
        self,
        target,
        *,
        version="",
        edition="ce",
        dry_run=False,
        **options,
    ):
        """Set up the project to run Odoo with Docker Compose."""
        service = options.get("service")
        command = options.get("command")
        compose_file = options.get("compose_file")

        todo = options.get("todo")

        if compose_file and not (target / compose_file).is_file():
            raise click.ClickException(
                f"Compose file '{compose_file}' not found in {target}."
            )

        if not compose_file:
            osh_compose = target / _COMPOSE_FILE
            if dry_run:
                info(
                    f"Would generate {osh_compose} with "
                    f"odoo/{version or 'latest'} and postgres:16 services.",
                    err=True,
                )
            else:
                if todo:
                    todo.start()
                _generate_compose_file(target, version)
                if osh_compose.is_file():
                    info(f"Generated {osh_compose}.", err=True)
            compose_file = str(_COMPOSE_FILE)

        copy_odoo_rc_to_osh_conf(target)

        if dry_run:
            info(
                f"Would write {target / _DOCKER_TOML}: "
                f"service={service or 'odoo'}, command={command or 'odoo-bin'}, "
                f"compose_file={compose_file or '<none>'}, "
                f"version={version!r}, edition={edition!r}.",
                err=True,
            )
            ensure_osh_sources(
                target,
                version,
                edition,
                dry_run=True,
                skip_odoo=True,
                assume_yes=options.get("assume_yes", False),
            )
            return True

        compose_tool = _find_compose_tool()
        if compose_tool is None:
            raise click.ClickException(
                "No Docker Compose tool found. "
                "Install 'docker compose' or 'docker-compose'."
            )

        if todo:
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
        info(
            f"Wrote Docker backend config to {target / _DOCKER_TOML}.",
            err=True,
        )
        if not service:
            warning(
                "no --service provided; defaulting to 'odoo'. "
                f"Edit {target / _DOCKER_TOML} if your compose service is named differently."
            )

        if todo:
            todo.start()
        ensure_osh_sources(
            target,
            version,
            edition,
            dry_run=False,
            skip_odoo=True,
            assume_yes=options.get("assume_yes", False),
        )

        cfg = _load_docker_config(target)
        svc = cfg.get("service")
        cmd = _docker_command(svc, cfg.get("command"))
        if not svc:
            warning("no Docker service configured; skipping smoke test.")
            return True

        if todo:
            todo.start()
        compose_cmd = _compose_base_command(target, compose_file=compose_file)
        try:
            run_command(
                [*compose_cmd, "run", "--rm", svc, *cmd, "--version"],
                cwd=target,
                check=True,
                stream=True,
            )
        except click.ClickException as exc:
            warning(
                f"{exc.format_message()}\n"
                "The project is initialised but Odoo may not be usable."
            )
            return False

        friendly(f"Run the project with: osh run (in {target})")
        return True

    def run(
        self,
        ctx,
        base,
        run_spec,
        *,
        dry_run=False,
        verbose=False,
        **options,
    ):
        """Translate host odoo-bin arguments into a Docker Compose invocation."""
        if ctx:
            get_echo(ctx, base, verbose_override=verbose)
        else:
            from ... import echo as echo_module

            echo_module._get_cached_echo()
        cfg = _load_docker_config(base)
        service = cfg.get("service")
        command = cfg.get("command")
        if not service:
            raise click.ClickException(
                "No Docker service configured. Run "
                "'osh init --target docker --service <name>' or edit "
                f"{base / _DOCKER_TOML}."
            )

        edition = options.get("edition") or cfg.get("edition") or "ce"
        version = cfg.get("version", "")

        if edition in ("ee", "sh") and not version:
            required = ["enterprise"]
            if edition == "sh":
                required.append("design-themes")
            missing = [name for name in required if not (base / ".osh" / name).exists()]
            if missing:
                raise click.ClickException(
                    "Project is missing required source copies and no version is "
                    "configured. Run 'osh init ...' first."
                )

        ensure_osh_sources(
            base,
            version,
            edition,
            dry_run=dry_run,
            skip_odoo=True,
            assume_yes=True,
        )

        if not isinstance(run_spec, RunSpec):
            run_spec = RunSpec(argv=list(run_spec))

        if not run_spec.argv:
            raise click.ClickException("No command provided to run.")
        odoo_args = run_spec.argv[1:]  # argv[0] is the host executable placeholder

        def _is_relative_to(path, base):
            try:
                path.relative_to(base)
                return True
            except ValueError:
                return False

        if "--addons-path" not in odoo_args:
            addons_paths = build_addons_paths(base, include_themes=True)
            container_paths = [
                f"/mnt/extra-addons/{p.relative_to(base)}"
                for p in addons_paths
                if _is_relative_to(p, base)
            ]
            if container_paths:
                odoo_args.append(f"--addons-path={','.join(container_paths)}")

        # Inject .osh/odoo.conf if it exists, otherwise fall back to .odoorc
        config_path = resolve_config_file(base, odoo_args)
        if config_path:
            # Convert local path to container path
            container_config = str(config_path).replace(str(base), "/mnt/extra-addons")
            odoo_args.append(f"--config={container_config}")

        odoo_command = _docker_command(service, command)
        cli_params = getattr(ctx, "params", {}) or {}
        compose_cmd = _compose_base_command(
            base, compose_file=cli_params.get("compose_file")
        )
        docker_args = [
            *compose_cmd,
            "run",
            "--rm",
            "--service-ports",
            service,
            *odoo_command,
            *odoo_args,
        ]

        if dry_run:
            info(f"Would run: {' '.join(docker_args)}", err=True)
            return

        if verbose:
            info(f"Running: {' '.join(docker_args)}", err=True)
        else:
            info(f"Running {' '.join(docker_args)}", err=True)

        try:
            os.execvp("docker", docker_args)
        except OSError as exc:  # pragma: no cover
            raise click.ClickException(f"Could not run docker: {exc}") from exc

    def restore(
        self,
        ctx,
        base,
        db_name,
        dump_path,
        *,
        force=False,
        no_neutralize=False,
        dry_run=False,
        **options,
    ):
        """Restore a backup into the target database through this backend."""
        raise click.ClickException("Docker restore is not yet implemented.")

    def neutralize(
        self,
        ctx,
        base,
        db_name,
        *,
        dry_run=False,
    ):
        """Neutralize *db_name* by running ``odoo-bin neutralize`` in the container."""
        self.run(
            ctx,
            base,
            RunSpec(argv=["odoo-bin", "-d", db_name, "neutralize"], db_name=db_name),
            dry_run=dry_run,
            verbose=False,
        )

    def prune(
        self,
        ctx,
        base,
        *,
        aggressive=False,
        dry_run=False,
        **options,
    ):
        """Run target-specific housekeeping."""
        raise click.ClickException("Docker prune is not yet implemented.")


def _cfg_value(cfg, key, default=None):
    """Return *key* from *cfg* when available, otherwise *default*."""
    return cfg.get(key, default) if cfg else default
