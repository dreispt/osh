"""Tests for the generated Odoo config and compose-file resolution."""

from click.testing import CliRunner

from osh.commands.odoo_cmd import odoo
from osh.commands.shell_cmd import build_dynamic_odoo_config
from osh.db import set_project_config
from osh.plugins.osh_runtime_docker.runtimes import DockerRuntime
from osh.runtimes import EnvSpec

from .conftest import _write_docker_config


def test_conf_addons_path_resolves_symlinks_on_host(tmp_project):
    """A symlinked source checkout maps to its real path in the conf.

    ``.osh/odoo`` may be a symlink to a source checkout (e.g. created by
    ``osh init`` linking a project-local clone). Translated literally it
    would dangle inside the container; resolving on the host first maps it
    to the real directory under ``/mnt/extra-addons``.
    """
    (tmp_project / "odoo" / "odoo" / "addons").mkdir(parents=True)
    (tmp_project / ".osh" / "odoo").symlink_to(
        tmp_project / "odoo" / "odoo", target_is_directory=True
    )

    conf = build_dynamic_odoo_config(tmp_project, "mydb", DockerRuntime())

    addons_path = next(
        line for line in conf.read_text().splitlines() if line.startswith("addons_path")
    )
    assert "/mnt/extra-addons/odoo/odoo/addons" in addons_path
    assert ".osh" not in addons_path


def test_docker_addons_paths_mount_out_of_project_sources(
    docker_daemon, tmp_project, tmp_path
):
    """Sources linked from outside the project get a ``/mnt/osh-src`` mount.

    A generated Compose override adds them as read-only volumes, since the
    ``/mnt/extra-addons`` project mount cannot reach them.
    """
    external = tmp_path / "shared-odoo"
    (external / "addons").mkdir(parents=True)
    (tmp_project / ".osh" / "odoo").symlink_to(external, target_is_directory=True)
    _write_docker_config(tmp_project)
    (tmp_project / ".osh" / "docker-compose.yml").write_text(
        "services:\n  odoo:\n    image: busybox\n    command: sleep infinity\n"
    )

    runtime = DockerRuntime()

    conf = build_dynamic_odoo_config(tmp_project, "mydb", runtime)
    container_path = (
        conf.read_text().split("addons_path = ", 1)[1].splitlines()[0].strip()
    )
    assert container_path.startswith("/mnt/osh-src/addons-")

    # The override is generated on ensure_service_up and carries the mount.
    runtime.ensure_service_up(tmp_project)

    override = tmp_project / ".osh" / "docker-compose.osh.yml"
    assert override.is_file()
    text = override.read_text()
    assert f"{external / 'addons'}:{container_path}:ro" in text


def test_conf_data_dir_only_when_declared(tmp_project):
    """``data_dir`` is written into the run conf only when declared."""
    runtime = DockerRuntime()

    # Nothing declared: no compose file, no docker.toml key.
    conf = build_dynamic_odoo_config(tmp_project, "mydb", runtime)
    assert "data_dir" not in conf.read_text()

    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.write_text('service = "odoo"\ndata_dir = "/opt/odoo/data"\n')
    conf = build_dynamic_odoo_config(tmp_project, "mydb", runtime)
    assert "data_dir = /opt/odoo/data" in conf.read_text()


def test_conf_data_dir_from_compose_env(tmp_project, docker_cli):
    """The service's ``ODOO_DATA_DIR`` lands in the generated config."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.write_text(
        'service = "odoo"\ncompose_tool = "docker compose"\n'
        'compose_file = "docker-compose.yml"\n'
    )
    (tmp_project / "docker-compose.yml").write_text(
        "services:\n  odoo:\n    image: odoo:19.0\n"
        "    environment:\n      ODOO_DATA_DIR: /odoo/data\n"
    )

    conf = build_dynamic_odoo_config(tmp_project, "mydb", DockerRuntime())

    assert "data_dir = /odoo/data" in conf.read_text()


def test_conf_data_dir_from_volume_mount(tmp_project, docker_cli):
    """A ``*/data`` or ``/var/lib/odoo`` volume target is honored."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.write_text(
        'service = "odoo"\ncompose_tool = "docker compose"\n'
        'compose_file = "docker-compose.yml"\n'
    )
    compose = tmp_project / "docker-compose.yml"
    runtime = DockerRuntime()

    for target in ("/odoo/data", "/var/lib/odoo"):
        compose.write_text(
            "services:\n  odoo:\n    image: odoo:19.0\n"
            f"    volumes:\n      - data:{target}\nvolumes:\n  data:\n"
        )
        conf = build_dynamic_odoo_config(tmp_project, "mydb", runtime)
        assert f"data_dir = {target}" in conf.read_text()

    # Unrelated mounts leave Odoo's own default untouched.
    compose.write_text(
        "services:\n  odoo:\n    image: odoo:19.0\n"
        "    volumes:\n      - .:/mnt/extra-addons\n"
    )
    conf = build_dynamic_odoo_config(tmp_project, "mydb", runtime)
    assert "data_dir" not in conf.read_text()


def test_build_dynamic_odoo_config_uses_container_paths_for_docker(
    tmp_project,
):
    """The helper translates local addon paths to the Docker mount point."""
    osh_dir = tmp_project / ".osh"
    (osh_dir / "odoo" / "addons").mkdir(parents=True, exist_ok=True)
    (osh_dir / "enterprise").mkdir(parents=True, exist_ok=True)

    runtime = DockerRuntime()
    conf = build_dynamic_odoo_config(tmp_project, "mydb", runtime)
    text = conf.read_text()
    assert "/mnt/extra-addons/.osh/odoo/addons" in text
    assert "/mnt/extra-addons/.osh/enterprise" in text
    assert "db_name = mydb" in text
    assert "dbfilter = ^mydb$" in text


def test_build_dynamic_odoo_config_data_dir(tmp_project, fake_docker):
    """The generated config carries the data dir the project declares."""
    (tmp_project / ".osh" / "docker.toml").write_text(
        "service = 'odoo'\ncompose_tool = 'docker compose'\n"
        "compose_file = 'docker-compose.yml'\n"
    )
    (tmp_project / "docker-compose.yml").write_text(
        "services:\n  odoo:\n    image: odoo:19.0\n"
        "    volumes:\n      - data:/odoo/data\nvolumes:\n  data:\n"
    )
    # Fallback for hosts without a real Docker binary (fakebin delegates
    # ``config`` to the real one when present).
    (fake_docker / "config.json").write_text(
        '{"services": {"odoo": {"volumes": '
        '[{"type": "volume", "source": "data", "target": "/odoo/data"}]}}}'
    )

    conf = build_dynamic_odoo_config(tmp_project, "mydb", DockerRuntime())
    assert "data_dir = /odoo/data" in conf.read_text()

    # A data_dir in the project's seed config always wins.
    (tmp_project / ".osh" / "odoo.conf").write_text(
        "[options]\ndata_dir = /custom/data\n"
    )
    conf = build_dynamic_odoo_config(tmp_project, "mydb", DockerRuntime())
    text = conf.read_text()
    assert "data_dir = /custom/data" in text
    assert "/odoo/data" not in text


def test_dynamic_config_translates_addons_path_for_docker(tmp_project):
    """The dynamic config uses container paths for the Docker runtime."""
    osh_dir = tmp_project / ".osh"
    (osh_dir / "odoo" / "addons").mkdir(parents=True, exist_ok=True)
    (osh_dir / "enterprise").mkdir(parents=True, exist_ok=True)
    (osh_dir / "design-themes").mkdir(parents=True, exist_ok=True)

    runtime = DockerRuntime()
    conf = build_dynamic_odoo_config(
        tmp_project,
        "mydb",
        runtime,
    )
    text = conf.read_text()
    assert "/mnt/extra-addons/.osh/odoo/addons" in text
    assert "/mnt/extra-addons/.osh/enterprise" in text
    assert "/mnt/extra-addons/.osh/design-themes" in text
    assert "db_name = mydb" in text
    assert "dbfilter = ^mydb$" in text


def test_dynamic_config_translates_registered_addon_path_for_docker(
    tmp_project,
    tmp_path,
):
    """A registered path outside the project mounts under ``/mnt/osh-src``."""
    dep = tmp_path / "payroll"
    (dep / "some_module").mkdir(parents=True)
    (dep / "some_module" / "__manifest__.py").write_text("{'name': 'Some Module'}\n")
    set_project_config(tmp_project, "addons", "paths", [str(dep)])

    runtime = DockerRuntime()
    conf = build_dynamic_odoo_config(
        tmp_project,
        "mydb",
        runtime,
    )
    assert "/mnt/osh-src/payroll-" in conf.read_text()


def test_docker_runtime_compose_file_from_config(tmp_project, capsys):
    """The compose file from docker.toml is passed with ``-f``."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        'service = "odoo"\ncommand = "odoo"\ncompose_file = "devel.yaml"\n'
        'compose_tool = "docker compose"\n'
    )

    runtime = DockerRuntime()
    runtime.env(None, tmp_project, EnvSpec(argv=["odoo"]), dry_run=True)

    err = capsys.readouterr().err
    assert "docker compose" in err
    assert "-f" in err and "devel.yaml" in err
    assert "\nexec " in err


def test_docker_runtime_compose_file_cli_override(tmp_project, capsys):
    """A compose file passed in the click context overrides config."""
    docker_toml = tmp_project / ".osh" / "docker.toml"
    docker_toml.parent.mkdir(parents=True, exist_ok=True)
    docker_toml.write_text(
        'service = "odoo"\ncommand = "odoo"\ncompose_file = "devel.yaml"\n'
        'compose_tool = "docker compose"\n'
    )

    class FakeCtx:
        params = {"compose_file": "test.yaml"}

    runtime = DockerRuntime()
    runtime.env(
        FakeCtx(),
        tmp_project,
        EnvSpec(argv=["odoo"]),
        dry_run=True,
    )

    err = capsys.readouterr().err
    assert "-f" in err and "test.yaml" in err


def test_odoo_compose_file_from_env_var(tmp_project, fake_docker, monkeypatch):
    """OSH_COMPOSE_FILE is honored like --compose-file for the docker target."""
    osh_dir = tmp_project / ".osh"
    (osh_dir / "docker.toml").write_text(
        'service = "odoo"\ncommand = "odoo"\ncompose_tool = "docker compose"\n'
    )
    (tmp_project / "devel.yaml").write_text("services:\n  odoo:\n")
    monkeypatch.setenv("OSH_COMPOSE_FILE", "devel.yaml")

    set_project_config(tmp_project, "run", "runtime", "docker")
    set_project_config(tmp_project, "db", "default", "mydb")
    monkeypatch.chdir(tmp_project)

    runner = CliRunner()
    result = runner.invoke(odoo, ["--dry-run"])

    assert result.exit_code == 0, result.output
    assert "devel.yaml" in result.output
