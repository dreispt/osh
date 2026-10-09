"""Tests for ``osh init --runtime=docker`` — config and stack generation."""

import click
import pytest
from click.testing import CliRunner

from osh.cli import main
from osh.config import get_user_preference
from osh.db import get_project_config, set_project_config
from osh.plugins.osh_runtime_docker.runtimes import DockerRuntime


def test_init_target_docker_via_main_writes_compose_file(
    tmp_project, fake_docker, monkeypatch
):
    """``osh init --runtime=docker`` writes docker.toml and generates compose."""
    monkeypatch.chdir(tmp_project)

    runner = CliRunner()
    result = runner.invoke(
        main, ["init", "--runtime", "docker", "19.0", "--service", "app"]
    )

    assert result.exit_code == 0, result.output
    docker_toml = tmp_project / ".osh" / "docker.toml"
    assert docker_toml.exists()
    text = docker_toml.read_text()
    assert "service = 'app'" in text
    assert "command = 'odoo'" in text
    assert "compose_file = '.osh/docker-compose.yml'" in text

    compose_file = tmp_project / ".osh" / "docker-compose.yml"
    assert compose_file.exists()
    compose_text = compose_file.read_text()
    assert "  app:" in compose_text
    assert 'image: "odoo:19.0"' in compose_text
    assert "image: postgres:16" in compose_text
    assert "..:/mnt/extra-addons" in compose_text
    assert "user: odoo" in compose_text
    assert "PGHOST: db" in compose_text
    assert "PGPASSWORD: myodoo" in compose_text
    assert not (tmp_project / "docker-compose.yml").exists()
    assert not (tmp_project / "Dockerfile").exists()


def test_init_docker_command_writes_config_and_compose(
    tmp_project, fake_docker, monkeypatch
):
    """``osh init --runtime=docker`` generates ``.osh/docker-compose.yml`` and config."""
    monkeypatch.chdir(tmp_project)

    runner = CliRunner()
    result = runner.invoke(
        main, ["init", "--runtime", "docker", "19.0", "--service", "odoo"]
    )

    assert result.exit_code == 0, result.output
    docker_toml = tmp_project / ".osh" / "docker.toml"
    assert docker_toml.exists()
    assert "compose_file = '.osh/docker-compose.yml'" in docker_toml.read_text()
    assert (tmp_project / ".osh" / "docker-compose.yml").exists()


def test_init_docker_overwrites_existing_osh_compose(
    tmp_project, fake_docker, monkeypatch
):
    """``.osh/docker-compose.yml`` is Osh-managed and regenerated on init."""
    monkeypatch.chdir(tmp_project)

    existing = tmp_project / ".osh" / "docker-compose.yml"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text("existing: compose\n")

    runner = CliRunner()
    result = runner.invoke(main, ["init", "--runtime", "docker", "19.0"])

    assert result.exit_code == 0, result.output
    compose_text = existing.read_text()
    assert 'image: "odoo:19.0"' in compose_text
    assert "user: odoo" in compose_text
    assert (
        "compose_file = '.osh/docker-compose.yml'"
        in (tmp_project / ".osh" / "docker.toml").read_text()
    )


def test_init_docker_updates_compose_for_a_different_version(
    tmp_project, fake_docker, monkeypatch
):
    """Re-initialising Docker with a new version updates the generated compose file."""
    monkeypatch.chdir(tmp_project)

    runner = CliRunner()
    result = runner.invoke(
        main, ["init", "--runtime", "docker", "19.0", "--service", "odoo"]
    )
    assert result.exit_code == 0, result.output
    compose = tmp_project / ".osh" / "docker-compose.yml"
    compose_text = compose.read_text()
    assert 'image: "odoo:19.0"' in compose_text
    assert "user: odoo" in compose_text

    result = runner.invoke(
        main, ["init", "--runtime", "docker", "20.0", "--service", "odoo"]
    )
    assert result.exit_code == 0, result.output
    compose_text = compose.read_text()
    assert 'image: "odoo:20.0"' in compose_text
    assert "user: odoo" in compose_text
    assert "version = '20.0'" in (tmp_project / ".osh" / "docker.toml").read_text()


def test_init_docker_includes_permission_fix(tmp_project, fake_docker, monkeypatch):
    """The generated compose file includes the user directive to run as odoo."""
    monkeypatch.chdir(tmp_project)

    runner = CliRunner()
    result = runner.invoke(
        main, ["init", "--runtime", "docker", "19.0", "--service", "odoo"]
    )

    assert result.exit_code == 0, result.output
    compose_file = tmp_project / ".osh" / "docker-compose.yml"
    compose_text = compose_file.read_text()

    # Check that the service runs as the odoo user
    assert "user: odoo" in compose_text


def test_init_docker_persists_provided_compose_file(
    tmp_project, fake_docker, monkeypatch
):
    """A provided ``--compose-file`` is persisted into docker.toml."""
    monkeypatch.chdir(tmp_project)

    (tmp_project / "devel.yaml").write_text("services:\n  odoo:\n    image: odoo\n")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "init",
            "--runtime",
            "docker",
            "19.0",
            "--service",
            "odoo",
            "--compose-file",
            "devel.yaml",
        ],
    )

    assert result.exit_code == 0, result.output
    docker_toml = tmp_project / ".osh" / "docker.toml"
    assert "compose_file = 'devel.yaml'" in docker_toml.read_text()
    assert not (tmp_project / ".osh" / "docker-compose.yml").exists()


def test_init_docker_detects_project_compose_file(
    tmp_project, fake_docker, monkeypatch
):
    """A compose file at the project root is used instead of generating one."""
    monkeypatch.chdir(tmp_project)
    (tmp_project / "compose.yaml").write_text("services:\n  odoo:\n")

    runner = CliRunner()
    result = runner.invoke(
        main, ["init", "--runtime", "docker", "19.0", "--service", "odoo"]
    )

    assert result.exit_code == 0, result.output
    docker_toml = tmp_project / ".osh" / "docker.toml"
    assert "compose_file = 'compose.yaml'" in docker_toml.read_text()
    assert not (tmp_project / ".osh" / "docker-compose.yml").exists()


def test_init_docker_detects_devel_yaml(tmp_project, fake_docker, monkeypatch):
    """A Doodba-style ``devel.yaml`` is detected like canonical names."""
    monkeypatch.chdir(tmp_project)
    (tmp_project / "devel.yaml").write_text("services:\n  odoo:\n")

    runner = CliRunner()
    result = runner.invoke(main, ["init", "--runtime", "docker", "19.0"])

    assert result.exit_code == 0, result.output
    docker_toml = tmp_project / ".osh" / "docker.toml"
    assert "compose_file = 'devel.yaml'" in docker_toml.read_text()


def test_init_docker_compose_detection_precedence(
    tmp_project, fake_docker, monkeypatch
):
    """Canonical names win over ``devel.yaml``; the choice is reported."""
    monkeypatch.chdir(tmp_project)
    (tmp_project / "docker-compose.yml").write_text("services:\n  odoo:\n")
    (tmp_project / "devel.yaml").write_text("services:\n  odoo:\n")

    runner = CliRunner()
    result = runner.invoke(main, ["init", "--runtime", "docker", "19.0"])

    assert result.exit_code == 0, result.output
    docker_toml = tmp_project / ".osh" / "docker.toml"
    assert "compose_file = 'docker-compose.yml'" in docker_toml.read_text()
    assert "devel.yaml" in result.output


def test_init_docker_dockerfile_generates_build_compose(
    tmp_project, fake_docker, monkeypatch
):
    """A project Dockerfile produces a generated compose file that builds it."""
    monkeypatch.chdir(tmp_project)
    (tmp_project / "Dockerfile").write_text("FROM odoo:19.0\n")

    runner = CliRunner()
    result = runner.invoke(main, ["init", "--runtime", "docker", "19.0"])

    assert result.exit_code == 0, result.output
    docker_toml = tmp_project / ".osh" / "docker.toml"
    toml_text = docker_toml.read_text()
    assert "compose_file = '.osh/docker-compose.yml'" in toml_text
    assert "dockerfile = 'Dockerfile'" in toml_text

    compose_text = (tmp_project / ".osh" / "docker-compose.yml").read_text()
    assert 'dockerfile: "Dockerfile"' in compose_text
    assert "image: odoo:" not in compose_text
    # The image may not define an 'odoo' user, so no user directive.
    assert "user:" not in compose_text
    assert "command:" in compose_text


def test_init_docker_dockerfile_option(tmp_project, fake_docker, monkeypatch):
    """``--dockerfile`` accepts a Dockerfile not at the project root name."""
    monkeypatch.chdir(tmp_project)
    docker_dir = tmp_project / "docker"
    docker_dir.mkdir()
    (docker_dir / "Dockerfile.dev").write_text("FROM odoo:19.0\n")

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "init",
            "--runtime",
            "docker",
            "19.0",
            "--dockerfile",
            "docker/Dockerfile.dev",
        ],
    )

    assert result.exit_code == 0, result.output
    docker_toml = tmp_project / ".osh" / "docker.toml"
    assert "dockerfile = 'docker/Dockerfile.dev'" in docker_toml.read_text()
    compose_text = (tmp_project / ".osh" / "docker-compose.yml").read_text()
    assert 'dockerfile: "docker/Dockerfile.dev"' in compose_text


def test_init_docker_dockerfile_outside_project_errors(
    tmp_project, tmp_path, fake_docker, monkeypatch
):
    """A Dockerfile outside the build context (the project root) is refused."""
    monkeypatch.chdir(tmp_project)
    (tmp_path / "Dockerfile").write_text("FROM odoo:19.0\n")

    runner = CliRunner()
    result = runner.invoke(
        main, ["init", "--runtime", "docker", "19.0", "--dockerfile", "../Dockerfile"]
    )

    assert result.exit_code != 0
    assert "outside the project directory" in result.output


def test_reinit_keeps_configured_compose_file(tmp_project, fake_docker, monkeypatch):
    """Re-running init keeps the project compose file it already uses."""
    monkeypatch.chdir(tmp_project)
    (tmp_project / "custom.yaml").write_text("services:\n  odoo:\n")
    (tmp_project / ".osh" / "docker.toml").write_text(
        "service = 'odoo'\ncompose_file = 'custom.yaml'\n"
    )

    runner = CliRunner()
    result = runner.invoke(main, ["init", "--runtime", "docker", "19.0"])

    assert result.exit_code == 0, result.output
    assert (
        "compose_file = 'custom.yaml'"
        in (tmp_project / ".osh" / "docker.toml").read_text()
    )
    assert not (tmp_project / ".osh" / "docker-compose.yml").exists()


def test_reinit_dockerfile_option_keeps_configured_compose(
    tmp_project, fake_docker, monkeypatch
):
    """``--dockerfile`` does not replace an already-configured compose file."""
    monkeypatch.chdir(tmp_project)
    (tmp_project / "compose.yaml").write_text("services:\n  odoo:\n")
    (tmp_project / "Dockerfile").write_text("FROM odoo:19.0\n")
    (tmp_project / ".osh" / "docker.toml").write_text(
        "service = 'odoo'\ncompose_file = 'compose.yaml'\n"
    )

    result = CliRunner().invoke(
        main, ["init", "--runtime", "docker", "19.0", "--dockerfile", "Dockerfile"]
    )

    assert result.exit_code == 0, result.output
    assert "Ignoring --dockerfile" in result.output
    toml_text = (tmp_project / ".osh" / "docker.toml").read_text()
    assert "compose_file = 'compose.yaml'" in toml_text
    assert "dockerfile" not in toml_text
    assert not (tmp_project / ".osh" / "docker-compose.yml").exists()


def test_reinit_regenerates_stack_over_detected_compose(
    tmp_project, fake_docker, monkeypatch
):
    """A compose file appearing later does not replace a generated stack."""
    monkeypatch.chdir(tmp_project)
    runner = CliRunner()

    result = runner.invoke(main, ["init", "--runtime", "docker", "19.0"])
    assert result.exit_code == 0, result.output

    (tmp_project / "compose.yaml").write_text("services:\n  odoo:\n")
    result = runner.invoke(main, ["init", "--runtime", "docker", "20.0"])

    assert result.exit_code == 0, result.output
    docker_toml = (tmp_project / ".osh" / "docker.toml").read_text()
    assert "compose_file = '.osh/docker-compose.yml'" in docker_toml
    generated = (tmp_project / ".osh" / "docker-compose.yml").read_text()
    assert 'image: "odoo:20.0"' in generated


def test_reinit_ignores_dockerfile_outside_project(
    tmp_project, tmp_path, fake_docker, monkeypatch
):
    """A configured Dockerfile outside the build context is not reused."""
    monkeypatch.chdir(tmp_project)
    # A Dockerfile at tmp_path is outside the project's build context.
    (tmp_path / "Dockerfile").write_text("FROM odoo:19.0\n")
    (tmp_project / ".osh" / "docker.toml").write_text(
        "service = 'odoo'\ndockerfile = '../Dockerfile'\n"
    )

    result = CliRunner().invoke(main, ["init", "--runtime", "docker", "19.0"])

    assert result.exit_code == 0, result.output
    compose_text = (tmp_project / ".osh" / "docker-compose.yml").read_text()
    assert "../Dockerfile" not in compose_text
    assert 'image: "odoo:19.0"' in compose_text
    assert "dockerfile" not in (tmp_project / ".osh" / "docker.toml").read_text()


def test_reinit_reports_missing_configured_compose_file(
    tmp_project, fake_docker, monkeypatch
):
    """A configured compose file that vanished is a clear init error."""
    monkeypatch.chdir(tmp_project)
    (tmp_project / ".osh" / "docker.toml").write_text("compose_file = 'gone.yaml'\n")

    result = CliRunner().invoke(main, ["init", "--runtime", "docker", "19.0"])

    assert result.exit_code != 0
    assert "gone.yaml" in result.output


def test_init_docker_missing_service_errors(tmp_project, fake_docker, monkeypatch):
    """A compose file without the configured service fails at init."""
    monkeypatch.chdir(tmp_project)
    (tmp_project / "compose.yaml").write_text("services:\n  web:\n    image: web\n")

    result = CliRunner().invoke(main, ["init", "--runtime", "docker", "19.0"])

    assert result.exit_code != 0
    assert "Service 'odoo' not found in compose.yaml" in result.output
    assert "web" in result.output


def test_init_docker_invalid_service_name_errors(tmp_project, fake_docker, monkeypatch):
    """A service name that would corrupt generated YAML is refused."""
    monkeypatch.chdir(tmp_project)

    result = CliRunner().invoke(
        main, ["init", "--runtime", "docker", "19.0", "--service", "bad name"]
    )

    assert result.exit_code != 0
    assert "Invalid Docker service name" in result.output


def test_init_docker_custom_service_on_foreign_compose(
    tmp_project, fake_docker, monkeypatch
):
    """A non-default ``--service`` is accepted when the file defines it."""
    monkeypatch.chdir(tmp_project)
    (tmp_project / "compose.yaml").write_text(
        "services:\n  app:\n    image: odoo:19.0\n"
    )

    result = CliRunner().invoke(
        main, ["init", "--runtime", "docker", "19.0", "--service", "app"]
    )

    assert result.exit_code == 0, result.output
    docker_toml = (tmp_project / ".osh" / "docker.toml").read_text()
    assert "service = 'app'" in docker_toml
    assert "compose_file = 'compose.yaml'" in docker_toml


def test_init_docker_dry_run_still_validates_service(tmp_project, docker_cli):
    """A dry-run init reports a missing service instead of failing on the real run."""
    (tmp_project / "compose.yaml").write_text("services:\n  web:\n    image: web\n")
    from osh.commands.init_cmd import TodoPlan

    runtime = DockerRuntime()
    with pytest.raises(click.ClickException, match="Service 'odoo' not found"):
        runtime.init(
            tmp_project,
            version="19.0",
            dry_run=True,
            compose_file="compose.yaml",
            todo=TodoPlan(None),
        )
    assert not (tmp_project / ".osh" / "docker.toml").exists()


def test_init_docker_dockerfile_missing_errors(tmp_project, fake_docker, monkeypatch):
    """A missing explicit ``--dockerfile`` raises an error."""
    monkeypatch.chdir(tmp_project)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["init", "--runtime", "docker", "19.0", "--dockerfile", "missing.Dockerfile"],
    )

    assert result.exit_code != 0
    assert "missing.Dockerfile" in result.output


def test_init_docker_missing_compose_file_raises(tmp_project, fake_docker, monkeypatch):
    """A missing explicit compose file raises an error."""
    monkeypatch.chdir(tmp_project)

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "init",
            "--runtime",
            "docker",
            "19.0",
            "--service",
            "odoo",
            "--compose-file",
            "missing.yaml",
        ],
    )

    assert result.exit_code != 0
    assert "missing.yaml" in result.output or "not found" in result.output


def test_init_docker_dry_run_does_not_write(tmp_project, docker_cli, monkeypatch):
    """A dry-run ``init`` only reports what it would generate."""
    runtime = DockerRuntime()

    from osh.commands.init_cmd import TodoPlan

    ok = runtime.init(
        tmp_project,
        version="19.0",
        edition="ce",
        dry_run=True,
        service="odoo",
        todo=TodoPlan(None),
    )

    assert ok is True
    assert not (tmp_project / ".osh" / "docker.toml").exists()
    assert not (tmp_project / ".osh" / "docker-compose.yml").exists()


def test_init_docker_writes_version_and_edition(tmp_project, fake_docker, monkeypatch):
    """``osh init --runtime=docker`` persists the Odoo version and edition."""
    monkeypatch.chdir(tmp_project)

    ent = tmp_project / "enterprise"
    (ent / "web").mkdir(parents=True, exist_ok=True)
    (ent / "web" / "__manifest__.py").touch()

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "init",
            "--runtime",
            "docker",
            "19.0",
            "--service",
            "odoo",
            "--ee",
            "--enterprise-source",
            str(ent),
        ],
    )

    assert result.exit_code == 0, result.output
    docker_toml = tmp_project / ".osh" / "docker.toml"
    text = docker_toml.read_text()
    assert "version = '19.0'" in text
    assert "edition = 'ee'" in text


def test_init_runtime_docker_records_run_runtime(in_project, fake_docker):
    """``osh init --runtime=docker`` makes 'docker' the active runtime."""
    set_project_config(in_project, "init", "version", "19.0")

    result = CliRunner().invoke(
        main, ["init", "--runtime", "docker", "--edition", "ce", "--yes"]
    )

    assert result.exit_code == 0, result.output
    assert get_project_config(in_project, "run", "runtime") == "docker"
    assert (in_project / ".osh" / "docker.toml").exists()


def test_init_runtime_passes_runtime_options(in_project, fake_docker):
    """Options owned by the selected runtime land in its config."""
    set_project_config(in_project, "init", "version", "19.0")

    result = CliRunner().invoke(
        main,
        [
            "init",
            "--runtime",
            "docker",
            "--service",
            "odoo",
            "--port",
            "9071",
            "--edition",
            "ce",
            "--yes",
        ],
    )

    assert result.exit_code == 0, result.output
    docker_toml = (in_project / ".osh" / "docker.toml").read_text()
    assert "service = 'odoo'" in docker_toml
    assert "port = 9071" in docker_toml


def test_init_uses_stored_runtime_default(in_project, fake_docker, user_config):
    """A bare ``osh init`` applies the stored runtime default."""
    set_project_config(in_project, "init", "version", "19.0")
    user_config.parent.mkdir(parents=True)
    user_config.write_text('[init]\nruntime = "docker"\n')

    result = CliRunner().invoke(
        main, ["init", "20.0", str(in_project), "--edition", "ce", "--yes"]
    )

    assert result.exit_code == 0, result.output
    assert get_project_config(in_project, "run", "runtime") == "docker"
    assert (in_project / ".osh" / "docker.toml").exists()


def test_init_explicit_runtime_becomes_default(in_project, fake_docker, user_config):
    """``--runtime=<name>`` is remembered as the user's default."""
    set_project_config(in_project, "init", "version", "19.0")

    result = CliRunner().invoke(
        main, ["init", "--runtime", "docker", "--edition", "ce", "--yes"]
    )

    assert result.exit_code == 0, result.output
    assert get_user_preference("runtime", section="init") == "docker"
