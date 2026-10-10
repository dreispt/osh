"""Fixtures for the Doodba runtime plugin tests."""

import json

import pytest

# The fake-Docker fixtures live in the Docker plugin's suite — both
# runtimes drive the same ``docker``/``docker compose`` CLI surface.
from osh.plugins.osh_runtime_docker.tests.conftest import (  # noqa: F401
    docker_cli,
    fake_docker,
)


@pytest.fixture
def doodba_project(tmp_project):
    """A minimal Doodba layout: devel stack, build context, src dir."""
    (tmp_project / "odoo" / "custom" / "src").mkdir(parents=True)
    (tmp_project / "odoo" / "Dockerfile").write_text(
        "FROM ghcr.io/tecnativa/doodba:19.0-onbuild\n"
    )
    (tmp_project / "devel.yaml").write_text(
        "services:\n"
        "  odoo:\n"
        "    build:\n"
        "      context: odoo\n"
        "      args:\n"
        "        ODOO_VERSION: '19.0'\n"
        "    depends_on:\n"
        "      - db\n"
        "  odoo_proxy:\n"
        "    image: nginx\n"
        "    ports:\n"
        '      - "127.0.0.1:19069:8069"\n'
        "  db:\n"
        "    image: postgres:16\n"
    )
    return tmp_project


@pytest.fixture
def doodba_config_json(request):
    """Canned ``compose config`` mirroring the doodba fixture's resolution.

    With a real Docker on PATH the fake CLI resolves the fixture's YAML
    itself; without one the canned output answers the same values.
    """
    state = request.getfixturevalue("fake_docker")
    (state / "config.json").write_text(
        json.dumps(
            {
                "services": {
                    "odoo": {"build": {"args": {"ODOO_VERSION": "19.0"}}},
                    "odoo_proxy": {"ports": [{"target": 8069, "published": "19069"}]},
                    "db": {"image": "postgres:16"},
                }
            }
        )
    )
    return state
