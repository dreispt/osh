"""Regression tests — each case reproduces an issue found in real use."""

from click.testing import CliRunner

from osh.cli import main


def test_reinit_honors_service_saved_in_docker_toml(
    tmp_project, fake_docker, monkeypatch
):
    """Re-init failed on a service the project never had.

    ``osh docker init`` on a project whose ``.osh/docker.toml`` already
    named the Odoo service (``app``, alongside ``db`` and ``mail`` in the
    project's compose file) failed with ``Service 'odoo' not found`` —
    init ignored the saved configuration and validated the default
    service name instead.
    """
    monkeypatch.chdir(tmp_project)
    (tmp_project / "compose.yaml").write_text(
        "services:\n  app:\n    image: odoo:19.0\n"
    )
    (tmp_project / ".osh" / "docker.toml").write_text(
        "service = 'app'\ncompose_file = 'compose.yaml'\n"
    )

    result = CliRunner().invoke(main, ["docker", "init", "19.0"])

    assert result.exit_code == 0, result.output
    docker_toml = (tmp_project / ".osh" / "docker.toml").read_text()
    assert "service = 'app'" in docker_toml


def test_streamed_command_output_is_not_styled(tmp_project, fake_docker, monkeypatch):
    """``compose build`` output during init printed in Osh's info color.

    Colorized message categories painted every streamed line cyan —
    subprocess output is command output, not an Osh message, so it must
    stay neutral while Osh's own lines keep their styling.
    """
    monkeypatch.chdir(tmp_project)
    (tmp_project / "compose.yaml").write_text(
        "services:\n  app:\n    build: .\n    image: odoo:19.0\n"
    )
    (tmp_project / ".osh" / "docker.toml").write_text(
        "service = 'app'\ncompose_file = 'compose.yaml'\n"
    )
    (fake_docker / "build.out").write_text(
        "#1 [internal] load build definition from Dockerfile\n"
    )

    result = CliRunner().invoke(main, ["docker", "init", "19.0"], color=True)

    assert result.exit_code == 0, result.output
    assert "\x1b[" in result.output  # Osh messages are styled
    streamed = "#1 [internal] load build definition from Dockerfile"
    assert streamed in result.output
    assert f"\x1b[36m{streamed}" not in result.output
