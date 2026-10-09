"""Docker label/container discovery helpers.

Helpers that query the Docker daemon for containers and Compose labels,
used by ``osh stop --all``, ``osh stop <name>``, port-collision
diagnostics and status reporting. Kept separate from the Compose
invocation helpers in ``utils.py``.
"""

import json
import re
import shutil
from pathlib import Path

import click

from ...common import run_subprocess


def _find_port_holder(port):
    """Identify an Osh-managed project holding *port* via Docker labels.

    Returns ``(project_path, running_for)`` when the port is published by a
    container belonging to another Osh Docker project, or ``None`` when the
    holder is not identifiable.
    """
    for c in _port_publishers(port):
        if c["project"] is not None:
            return str(c["project"]), c["running_for"] or "unknown uptime"
    return None


def _port_publishers(port):
    """Return the containers publishing host *port*, with owner details.

    One ``docker ps`` call, answered as ``{id, name, image, running_for,
    compose_project, project}`` dicts — ``project`` is the resolved Osh
    project ``Path``, or ``None`` when the container is not part of an
    Osh-managed stack. The ``Ports`` column is checked client-side so
    Podman and published port ranges are covered too.
    """
    returncode, out, _ = run_subprocess(
        [
            _docker_executable(),
            "ps",
            "--format",
            "{{json .}}",
        ]
    )
    if returncode != 0 or not out:
        return []
    published = re.compile(rf":{int(port)}(?:-\d+)?->")
    containers = []
    for line in out.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not published.search(str(entry.get("Ports") or "")):
            continue
        labels = entry.get("Labels") or ""
        working_dir = _label_value(labels, "com.docker.compose.project.working_dir")
        containers.append(
            {
                "id": entry.get("ID") or "",
                "name": entry.get("Names") or "",
                "image": entry.get("Image") or "",
                "running_for": (entry.get("RunningFor") or "").strip(),
                "compose_project": _label_value(labels, "com.docker.compose.project"),
                "project": (
                    _osh_project_for_working_dir(Path(working_dir))
                    if working_dir
                    else None
                ),
            }
        )
    return containers


def _list_containers(show_all=False):
    """Return the running Docker containers with their owning project.

    Runs ``docker ps --format '{{json .}}'`` (``-a`` when *show_all*) and
    returns a list of dicts with ``id``, ``name``, ``image``, ``ports``,
    ``status``, ``compose_project``, ``working_dir``, ``config_file`` and
    ``project`` — the resolved Osh project ``Path``, or ``None`` when the
    container is not part of an Osh-managed stack.
    """
    docker = _docker_executable()
    args = [docker, "ps", "--format", "{{json .}}"]
    if show_all:
        args.insert(2, "-a")
    _, out, _ = run_subprocess(args, error_msg="Could not list Docker containers")
    containers = []
    for line in out.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        labels = entry.get("Labels") or ""
        working_dir = _label_value(labels, "com.docker.compose.project.working_dir")
        containers.append(
            {
                "id": entry.get("ID") or "",
                "name": entry.get("Names") or "",
                "image": entry.get("Image") or "",
                "ports": entry.get("Ports") or "",
                "status": entry.get("Status") or "",
                "compose_project": _label_value(labels, "com.docker.compose.project"),
                "working_dir": working_dir,
                "config_file": _label_value(
                    labels, "com.docker.compose.project.config_files"
                ),
                "project": (
                    _osh_project_for_working_dir(Path(working_dir))
                    if working_dir
                    else None
                ),
            }
        )
    return containers


def _project_stacks():
    """Return every Osh-managed Compose stack found via container labels.

    Stacks are keyed by ``{path, osh, compose_projects, config_files, ids}`` —
    ``path`` is the verified Osh project (``osh`` True) or the best-guess
    path from the working-dir label of a container whose ``osh-*`` Compose
    project survived its deleted directory. A single project may run
    containers under several Compose project names (stacks started by
    older Osh versions or outside ``osh odoo``).
    """
    stacks = {}
    for c in _list_containers(show_all=True):
        project = c["project"]
        if project is not None:
            path = project
            key = f"path:{project}"
        elif (c["compose_project"] or "").startswith("osh-") and c["working_dir"]:
            wd = Path(c["working_dir"])
            path = wd.parent if wd.name == ".osh" else wd
            key = f"compose:{c['compose_project']}"
        else:
            continue
        stack = stacks.setdefault(
            key,
            {
                "path": path,
                "osh": False,
                "compose_projects": set(),
                "config_files": [],
                "ids": [],
            },
        )
        if c["compose_project"]:
            stack["compose_projects"].add(c["compose_project"])
        for file in (c["config_file"] or "").split(","):
            if file and file not in stack["config_files"]:
                stack["config_files"].append(file)
        stack["ids"].append(c["id"])
        if project is not None:
            stack["osh"] = True
            stack["path"] = project
    return stacks


def _find_project_stack(name):
    """Locate the Compose stack of the Osh project named *name*.

    *name* is the project directory name shown by ``osh stop --all``; a
    full project path or an ``osh-*`` Compose project name also match.
    Returns the matching ``_project_stacks`` entry. Raises
    ``ClickException`` when nothing matches or the name is ambiguous.
    """
    resolved_name = Path(name).expanduser().resolve()
    stacks = {
        key: stack
        for key, stack in _project_stacks().items()
        if name in (stack["path"].name, str(stack["path"]))
        or name in stack["compose_projects"]
        or stack["path"].resolve() == resolved_name
    }

    if not stacks:
        raise click.ClickException(
            f"No Docker stack found for project '{name}'. "
            "See 'osh stop --all' for running projects."
        )
    if len(stacks) > 1:
        choices = "\n".join(
            f"  {stack['path']}"
            for stack in sorted(stacks.values(), key=lambda s: str(s["path"]))
        )
        raise click.ClickException(
            f"'{name}' matches more than one Docker project:\n{choices}\n"
            "Use the full project path instead."
        )
    return next(iter(stacks.values()))


def _port_process_hint(port):
    """Identify a host process listening on *port*, for error messages.

    Returns e.g. ``"'/p/.venv/bin/odoo --dev=all' (pid 123) in /p"`` — the
    process's working directory points at the project where ``osh stop``
    would free the port.
    """
    from ...runtimes import _looks_like_odoo, _pid_command, _pid_cwd, _port_listeners

    for pid in _port_listeners(port):
        cmdline = _pid_command(pid)
        if not cmdline:
            continue
        hint = f"'{cmdline}' (pid {pid})"
        if not _looks_like_odoo(cmdline):
            return hint
        cwd = _pid_cwd(pid)
        return f"host Odoo process {hint}" + (f" in {cwd}" if cwd else "")
    return None


def _project_containers(base):
    """Return ``_list_containers`` entries owned by the Osh project *base*."""
    resolved = Path(base).resolve()
    return [
        c
        for c in _list_containers(show_all=True)
        if c["project"] is not None and c["project"].resolve() == resolved
    ]


def _compose_project_names_for(base):
    """Return the Compose project names of *base*'s containers, if any.

    Stacks started outside ``osh odoo`` — or before the ``-p`` naming —
    carry a project label different from the derived one; this discovers
    what is really running so commands can target it.
    """
    return {
        c["compose_project"] for c in _project_containers(base) if c["compose_project"]
    }


def _container_running_status(base, compose_cmd, service):
    """Return ``(running, uptime)`` for *service*'s container.

    ``running`` is ``None`` when the state cannot be determined (e.g. the
    Compose tool cannot answer ``ps --format json``).
    """
    returncode, out, _ = run_subprocess(
        [*compose_cmd, "ps", "--format", "json", service], cwd=base
    )
    if returncode != 0:
        return None, ""
    for line in out.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if service and entry.get("Service") not in (None, service):
            continue
        if entry.get("State") == "running":
            status = entry.get("Status") or ""
            uptime = status[3:].strip() if status.startswith("Up ") else status
            return True, uptime
        return False, ""
    return False, ""


def _label_value(labels, name):
    """Return the value of *name* from Docker/Podman label data.

    Docker's ``--format '{{json .}}'`` reports Labels as a comma-separated
    ``key=value`` string; Podman reports a JSON object. Accept both.
    """
    if isinstance(labels, dict):
        return labels.get(name)
    if not labels:
        return None
    for item in re.split(r",(?=[^,]+=)", str(labels)):
        key, _, value = item.partition("=")
        if key.strip() == name:
            return value.strip()
    return None


def _osh_project_for_working_dir(working_dir):
    """Return the Osh project path for a Compose working dir, or None.

    The generated compose file lives in ``.osh/``, so its working directory is
    ``<project>/.osh``; custom compose files place it at the project root.
    """
    wd = Path(working_dir)
    if wd.name == ".osh" and (wd / "docker.toml").exists():
        return wd.parent
    if (wd / ".osh" / "docker.toml").exists():
        return wd
    return None


def _docker_executable():
    """Return the container CLI to use, preferring ``docker`` over ``podman``."""
    return shutil.which("docker") or shutil.which("podman") or "docker"
