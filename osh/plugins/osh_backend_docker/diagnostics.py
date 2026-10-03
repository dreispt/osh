"""Diagnostics for the Docker Compose backend.

Implements the ``osh doctor``/init/run health-check sections for
``DockerBackend``; the backend's ``diagnose`` method delegates here so the
backend module stays focused on the run lifecycle.
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path

from ...commands.helpers import Diagnostics
from ...common import run_subprocess
from .discovery import _container_running_status, _docker_executable
from .utils import (
    _COMPOSE_FILE,
    _compose_base_command,
    _detect_compose_file,
    _detect_dockerfile,
    _find_compose_tool,
    _load_docker_config,
    _resolve_compose_file,
)


def diagnose(backend, base, *, sections=None, **options):
    """Inspect Docker Compose environment and project configuration."""
    phase = options.get("phase", "doctor")
    d = Diagnostics(backend.name, project=base)

    if sections is None:
        sections = backend._DIAGNOSE_SECTIONS
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
        _diagnose_odoo_version(backend, d, phase, base)
    if "service" in sections:
        _diagnose_service(d, phase, service)
    if "container" in sections and cfg:
        _diagnose_container(d, base, service or "odoo", cfg)
    if "build" in sections and phase in ("run", "doctor"):
        backend._check_stale_environment(base, d)
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
    """Report the saved Docker backend configuration."""
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
        d.add_warning(
            "Docker backend config not found; it will be created during init."
        )
    elif phase == "run":
        d.add_error("Docker backend config not found. Run 'osh docker init' first.")
    else:
        d.add_warning("Docker backend config not found. Run 'osh docker init'.")


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
        d.add_plan(f"Generate {base / _COMPOSE_FILE}{suffix}")
    elif phase == "run":
        d.add_error(f"Compose file not found: {base / _COMPOSE_FILE}")
    else:
        d.add_warning(f"Compose file not found: {base / _COMPOSE_FILE}")


def _diagnose_odoo_version(backend, d, phase, base):
    """Detect and record the installed Odoo version."""
    odoo_version = backend.detect_odoo_version(base)
    if odoo_version:
        d.add_info("odoo_version", odoo_version)
    elif phase == "doctor":
        d.add_warning("Could not determine installed Odoo version.")


def _diagnose_service(d, phase, service):
    """Validate the configured Docker Compose service."""
    if not service:
        if phase == "init":
            d.add_warning("No --service provided; defaulting to 'odoo'.")
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


def _environment_build_groups(base, compose_file=None, cfg=None):
    """Return ``(image_built_epoch, context_file_paths)`` per buildable service.

    ``input_paths`` are lazy iterables so the shared staleness check walks
    each context only while comparing. Services whose image was never
    built are skipped — a cold ``up`` builds them, nothing is stale.
    """
    resolved = _resolve_compose_file(base, compose_file, cfg=cfg)
    if not resolved:
        return []
    compose_path = Path(resolved)
    if not compose_path.is_absolute():
        compose_path = base / compose_path
    try:
        # Cheap gate — image-only stacks have no ``build:`` to inspect.
        if "build" not in compose_path.read_text():
            return []
    except OSError:
        return []
    compose_cmd = _compose_base_command(
        base, compose_file=compose_file, cfg=cfg, required=False
    )
    if compose_cmd is None:
        return []
    returncode, out, _ = run_subprocess([*compose_cmd, "config", "--format", "json"])
    if returncode or not out:
        return []
    try:
        config = json.loads(out)
    except ValueError:
        return []
    project = config.get("name") or ""
    groups = []
    for name, svc in (config.get("services") or {}).items():
        build = svc.get("build")
        if not build:
            continue
        # The default image name for build-only services is
        # ``<project>-<service>``; the ``_`` form covers older Compose.
        created = None
        for image in (
            svc.get("image"),
            f"{project}-{name}",
            f"{project}_{name}",
        ):
            if image:
                created = _image_created(image)
            if created is not None:
                break
        if created is not None:
            groups.append((created, _build_context_files(build, compose_path.parent)))
    return groups


def _image_created(image):
    """Return *image*'s build time as epoch seconds, or None when absent."""
    returncode, out, _ = run_subprocess(
        [
            _docker_executable(),
            "image",
            "inspect",
            image,
            "--format",
            "{{.Created}}",
        ]
    )
    if returncode or not out:
        return None
    text = out.strip().replace("Z", "+00:00")
    if "." in text:
        # Python < 3.11 requires exactly 3 or 6 fractional digits in
        # fromisoformat; Docker emits up to 9 (nanoseconds).
        head, _, tail = text.partition(".")
        offset_at = next((i for i, c in enumerate(tail) if not c.isdigit()), len(tail))
        text = f"{head}.{tail[:offset_at][:6]}{tail[offset_at:]}"
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _build_context_files(build, compose_dir):
    """Yield *build*'s Dockerfile, then every file inside its context.

    Relative ``context``/``dockerfile`` values resolve against the Compose
    file's directory; ``.dockerignore`` exclusions are honoured.
    """
    context = Path(build.get("context") or ".")
    if not context.is_absolute():
        context = Path(compose_dir) / context
    dockerfile = Path(build.get("dockerfile") or "Dockerfile")
    if not dockerfile.is_absolute():
        dockerfile = context / dockerfile
    yield dockerfile
    rules = _dockerignore_rules(context)
    # A ``!`` rule may re-include files under an excluded directory, so
    # directories are pruned only when no negations are in play.
    can_prune = not any(negated for negated, _, _ in rules)
    for dirpath, dirnames, filenames in os.walk(context):
        dirpath = Path(dirpath)
        if can_prune:
            dirnames[:] = [
                name
                for name in dirnames
                if not _dockerignored(
                    (dirpath / name).relative_to(context).as_posix(), rules
                )
            ]
        for name in filenames:
            path = dirpath / name
            if _dockerignored(path.relative_to(context).as_posix(), rules):
                continue
            yield path


def _dockerignore_rules(context):
    """Return compiled ``.dockerignore`` rules for *context*.

    Each rule is ``(negated, has_slash, regex)`` — patterns without a ``/``
    match any path segment, like ``.gitignore``.
    """
    try:
        lines = (context / ".dockerignore").read_text().splitlines()
    except OSError:
        return ()
    rules = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        pattern = line.lstrip("!").lstrip("/").rstrip("/")
        if pattern:
            rules.append((negated, "/" in pattern, _dockerignore_regex(pattern)))
    return rules


def _dockerignore_regex(pattern):
    """Compile a ``.dockerignore`` pattern to a regex on posix paths."""
    i, out = 0, ""
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:[^/]*/)*"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        elif pattern[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(out + r"\Z")


def _dockerignored(rel, rules):
    """Whether relative POSIX path *rel* is excluded by *rules*."""
    ignored = False
    parts = rel.split("/")
    ancestors = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
    for negated, has_slash, regex in rules:
        candidates = ancestors if has_slash else parts
        if any(regex.fullmatch(c) for c in candidates):
            ignored = not negated
    return ignored


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
