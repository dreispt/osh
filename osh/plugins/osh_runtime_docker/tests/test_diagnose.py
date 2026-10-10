"""Tests for ``DockerRuntime.diagnose`` — stack and version reporting."""

from osh.plugins.osh_runtime_docker.runtimes import DockerRuntime

from .conftest import _compose


def test_diagnose_reports_odoo_version_from_docker_toml(tmp_project, docker_cli):
    """The recorded version is reported when sources and image tags miss."""
    (tmp_project / ".osh" / "docker.toml").write_text(
        "service = 'odoo'\nversion = '19.0'\n"
    )

    d = DockerRuntime().diagnose(tmp_project)

    assert d.info["docker"]["odoo_version"] == "19.0"


def test_docker_diagnose_reports_odoo_version_from_sources(tmp_project, docker_cli):
    """DockerRuntime.diagnose reports the Odoo version from .osh/odoo sources."""
    release = tmp_project / ".osh" / "odoo" / "odoo" / "release.py"
    release.parent.mkdir(parents=True, exist_ok=True)
    release.write_text('version = "21.0"\n')

    runtime = DockerRuntime()
    d = runtime.diagnose(tmp_project)
    assert d.info["docker"]["odoo_version"] == "21.0"


def test_diagnose_reports_odoo_version_from_compose_image(tmp_project, docker_cli):
    """The Odoo version is read from the generated stack's image tag."""
    compose = tmp_project / ".osh" / "docker-compose.yml"
    compose.parent.mkdir(parents=True, exist_ok=True)
    compose.write_text("services:\n  odoo:\n    image: odoo:17.0\n")

    d = DockerRuntime().diagnose(tmp_project)

    assert d.info["docker"]["odoo_version"] == "odoo 17.0"


def test_docker_runtime_diagnose(tmp_project, docker_cli):
    """``diagnose`` returns diagnostics for the configured stack."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        "service = 'odoo'\ncommand = 'odoo'\ncompose_file = 'devel.yaml'\n"
    )

    runtime = DockerRuntime()
    d = runtime.diagnose(tmp_project)

    assert d.info["docker"]["compose_file"] == "devel.yaml"
    assert d.info["docker"]["service"] == "odoo"
    assert d.info["docker"]["command"] == "odoo"


def test_docker_runtime_diagnose_honors_custom_compose_file(tmp_project, docker_cli):
    """``diagnose`` resolves the effective compose file from config/options."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        "service = 'odoo'\ncommand = 'odoo'\ncompose_file = 'devel.yaml'\n"
    )
    (tmp_project / "devel.yaml").write_text("services:\n  odoo:\n")

    runtime = DockerRuntime()
    d = runtime.diagnose(tmp_project, phase="run")

    assert d.ready
    assert not d.errors
    assert "resolved_compose_file" in d.info["docker"]
    assert str(tmp_project / "devel.yaml") in d.info["docker"]["resolved_compose_file"]


def test_docker_runtime_diagnose_ee_sources_missing_with_version(
    tmp_project, docker_cli
):
    """``diagnose`` allows missing source copies when a version is configured."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        "service = 'odoo'\ncommand = 'odoo'\nedition = 'sh'\nversion = '19.0'\n"
    )
    (tmp_project / ".osh" / "docker-compose.yml").write_text("services:\n  odoo:\n")

    runtime = DockerRuntime()
    d = runtime.diagnose(tmp_project, phase="run")

    assert d.ready
    assert not d.errors


def test_docker_runtime_diagnose_reports_container_state(docker_project):
    """``diagnose`` reports leftover container state so users notice it."""
    runtime = DockerRuntime()

    _compose(docker_project, "up", "-d")
    container = runtime.diagnose(docker_project, phase="run").info["docker"][
        "container"
    ]
    assert container.startswith("running, started")

    _compose(docker_project, "stop")
    container = runtime.diagnose(docker_project, phase="run").info["docker"][
        "container"
    ]
    assert container == "not running"


def test_docker_diagnose_warns_when_build_input_changes(
    docker_shared_project,
):
    """A build-input edit after the image build prompts a re-init hint.

    The shared fixture's ``app`` image was fingerprinted when its stack
    came up, so nothing is stale until a file it was built from changes —
    addon trees under the context do not count: they are mounted over at
    run time.
    """
    runtime = DockerRuntime()
    warnings = runtime.diagnose(docker_shared_project, phase="run").warnings
    assert not any("osh init docker" in w for w in warnings)

    dockerfile = docker_shared_project / "odoo" / "Dockerfile"
    dockerfile.write_text(dockerfile.read_text() + "# edited\n")

    warnings = runtime.diagnose(docker_shared_project, phase="run").warnings
    assert any("osh init docker" in w for w in warnings)
