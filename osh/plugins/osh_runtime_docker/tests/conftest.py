"""Fixtures for the Docker runtime plugin tests."""

import json
import os

FAKEBIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fakebin")


def _write_docker_config(project, port=None):
    """Write a minimal docker runtime config and generated compose file."""
    osh_dir = project / ".osh"
    osh_dir.mkdir(parents=True, exist_ok=True)
    text = 'service = "odoo"\ncommand = "odoo"\ncompose_tool = "docker compose"\n'
    if port:
        text += f"port = {port}\n"
    (osh_dir / "docker.toml").write_text(text)
    (osh_dir / "docker-compose.yml").write_text("services:\n  odoo:\n")


def _docker_ps_line(name, image, ports, status, labels="", cid=None):
    """Return a ``docker ps --format '{{json .}}'`` output line."""
    return json.dumps(
        {
            "ID": cid or name,
            "Names": name,
            "Image": image,
            "Ports": ports,
            "Status": status,
            "Labels": labels,
        }
    )


def _running_containers(fake_docker, *lines):
    """Feed the canned-answer ``docker`` a ``docker ps`` listing."""
    (fake_docker / "docker_ps").write_text("\n".join(lines))


def _docker_calls(fake_docker):
    """Return the argv lines the canned-answer ``docker`` recorded."""
    log = fake_docker / "calls.log"
    return log.read_text().splitlines() if log.exists() else []
