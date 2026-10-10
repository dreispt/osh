"""Diagnostics for the Docker Compose runtime.

Implements the ``osh doctor``/init/run health-check sections for
``DockerRuntime``; the runtime's ``diagnose`` method delegates here so the
runtime module stays focused on the run lifecycle.
"""

import json
from pathlib import Path

from ...commands.helpers import Diagnostics
from ...common import run_subprocess
from ...runtimes import BuildInputs
from .discovery import _container_running_status
from .utils import (
    _COMPOSE_FILE,
    _compose_base_command,
    _detect_compose_file,
    _detect_dockerfile,
    _find_compose_tool,
    _load_docker_config,
    _resolve_compose_file,
)


def diagnose(runtime, base, *, sections=None, **options):
    """Inspect Docker Compose environment and project configuration."""
    phase = options.get("phase", "doctor")
    d = Diagnostics(runtime.name, project=base)

    if sections is None:
        sections = runtime._DIAGNOSE_SECTIONS
    sections = set(sections)

    cfg = _load_docker_config(base)
    service = options.get("service") or cfg.get("service")
    command = options.get("command") or cfg.get("command")
    compose_file = options.get("compose_file") or cfg.get("compose_file")
    dockerfile = options.get("dockerfile") or cfg.get("dockerfile")
    edition = (options.get("edition") or cfg.get("edition") or "ce").lower()
    version = options.get("version") or cfg.get("version") or ""

    if "compose_tool" in sections:
        _diagnose_compose_tool(d, phase, cfg)
    if "config" in sections:
        _diagnose_config(
            d, phase, cfg, service, command, compose_file, dockerfile, edition
        )
    if "compose_file" in sections:
        _diagnose_compose_file(d, phase, base, compose_file, dockerfile, cfg)
    if "odoo_version" in sections:
        _diagnose_odoo_version(runtime, d, phase, base)
    if "service" in sections:
        _diagnose_service(d, phase, service)
    if "container" in sections and cfg:
        _diagnose_container(d, base, service or "odoo", cfg)
    if "build" in sections and phase in ("run", "doctor"):
        runtime._check_stale_environment(base, d, compose_file=compose_file)
    if (
        "sources" in sections
        and phase == "run"
        and edition in ("ee", "sh")
        and not version
    ):
        _diagnose_sources(d, base, edition)

    return d


def _diagnose_compose_tool(d, phase, cfg):
    """Detect and record the available Docker Compose tool."""
    cached_tool = cfg.get("compose_tool") if cfg else None
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


def _diagnose_config(
    d, phase, cfg, service, command, compose_file, dockerfile, edition
):
    """Report the saved Docker runtime configuration."""
    if cfg:
        d.add_info("service", service or "odoo")
        d.add_info("command", command or "odoo-bin")
        d.add_info("compose_file", compose_file or "<none>")
        if dockerfile:
            d.add_info("dockerfile", dockerfile)
        d.add_info("edition", edition)
        if cfg.get("db_service"):
            d.add_info("db_service", cfg["db_service"])
        if cfg.get("compose_tool"):
            d.add_info("configured_compose_tool", cfg["compose_tool"])
    elif phase == "init":
        d.add_note("Docker runtime config will be created during init.")
    elif phase == "run":
        d.add_error("Docker runtime config not found. " "Run 'osh init docker' first.")
    else:
        d.add_warning("Docker runtime config not found. Run 'osh init docker'.")


def _diagnose_compose_file(d, phase, base, compose_file, dockerfile=None, cfg=None):
    """Check the resolved Docker Compose file."""
    if phase == "init" and not compose_file:
        detected = _detect_compose_file(base)
        if len(detected) > 1:
            d.add_warning(
                "Multiple compose files found: "
                + ", ".join(detected)
                + f"; using {detected[0]}. "
                "Pass --compose-file to use another one."
            )
    resolved = _resolve_compose_file(base, compose_file, cfg=cfg)
    if resolved:
        compose_path = Path(resolved)
        if not compose_path.is_absolute():
            compose_path = base / compose_path
        if compose_path.exists():
            d.add_info("resolved_compose_file", str(compose_path))
        elif phase == "doctor":
            d.add_warning(f"Compose file not found: {compose_path}")
        else:
            d.add_error(f"Compose file not found: {compose_path}")
        return
    if phase == "init":
        dockerfile = dockerfile or _detect_dockerfile(base)
        suffix = f" building {dockerfile}" if dockerfile else ""
        d.add_plan(f"Compose file: generate {base / _COMPOSE_FILE}{suffix}")
    elif phase == "run":
        d.add_error(f"Compose file not found: {base / _COMPOSE_FILE}")
    else:
        d.add_warning(f"Compose file not found: {base / _COMPOSE_FILE}")


def _diagnose_odoo_version(runtime, d, phase, base):
    """Detect and record the installed Odoo version."""
    odoo_version = runtime.detect_odoo_version(base)
    if odoo_version:
        d.add_info("odoo_version", odoo_version)
    elif phase == "doctor":
        d.add_warning("Could not determine installed Odoo version.")


def _diagnose_service(d, phase, service):
    """Validate the configured Docker Compose service."""
    if not service:
        if phase == "init":
            d.add_note("No --service provided; using the default 'odoo'.")
        elif phase == "run":
            d.add_error("No Docker service configured.")


def _diagnose_container(d, base, service, cfg=None):
    """Report whether the project's service container is running."""
    compose_cmd = _compose_base_command(base, cfg=cfg, required=False)
    if compose_cmd is None:
        return
    running, uptime = _container_running_status(base, compose_cmd, service)
    if running is None:
        return
    if running:
        detail = f"running, started {uptime} ago" if uptime else "running"
        d.add_info("container", detail)
    else:
        d.add_info("container", "not running")


def _environment_input_groups(base, compose_file=None, cfg=None):
    """Return ``{service: BuildInputs}`` for the stack's buildable services.

    An image's fingerprint covers its Dockerfile, the files at the top of
    its build context, every ``requirements*.txt`` below it, and the
    resolved ``build:`` options. ``build_inputs`` in ``docker.toml`` adds
    context-relative globs for inputs kept deeper in the tree. Other
    context directories are skipped: a service usually COPYs in addon
    sources, which the project mount overrides at run time anyway.
    """
    resolved = _resolve_compose_file(base, compose_file, cfg=cfg)
    if not resolved:
        return {}
    compose_path = Path(resolved)
    if not compose_path.is_absolute():
        compose_path = base / compose_path
    try:
        # Cheap gate — image-only stacks have no ``build:`` to inspect.
        if "build" not in compose_path.read_text():
            return {}
    except OSError:
        return {}
    compose_cmd = _compose_base_command(
        base, compose_file=compose_file, cfg=cfg, required=False
    )
    if compose_cmd is None:
        return {}
    returncode, out, _ = run_subprocess([*compose_cmd, "config", "--format", "json"])
    if returncode or not out:
        return {}
    try:
        config = json.loads(out)
    except ValueError:
        return {}
    input_globs = (cfg or {}).get("build_inputs") or []
    if isinstance(input_globs, str):
        input_globs = [input_globs]
    groups = {}
    for name, svc in (config.get("services") or {}).items():
        build = svc.get("build")
        if not build:
            continue
        context = Path(build.get("context") or ".")
        if not context.is_absolute():
            context = compose_path.parent / context
        dockerfile = Path(build.get("dockerfile") or "Dockerfile")
        if not dockerfile.is_absolute():
            dockerfile = context / dockerfile
        paths = [dockerfile]
        try:
            paths.extend(path for path in context.iterdir() if path.is_file())
        except OSError:
            pass
        paths.extend(
            path for path in context.rglob("requirements*.txt") if path.is_file()
        )
        for pattern in input_globs:
            paths.extend(path for path in context.glob(pattern) if path.is_file())
        groups[name] = BuildInputs(paths=list(dict.fromkeys(paths)), spec=build)
    return groups


def _diagnose_sources(d, base, edition):
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
