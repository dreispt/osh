"""Tests for ``osh init doodba`` — the experimental Doodba runtime."""

import os
import stat

from click.testing import CliRunner

from osh.cli import main
from osh.db import set_project_config
from osh.plugins.osh_runtime_doodba.runtimes import DoodbaRuntime


def _calls(fake_docker):
    """Return the recorded fake-docker invocations, one per line."""
    log = fake_docker / "calls.log"
    return log.read_text().splitlines() if log.exists() else []


def _write_fake_copier(bin_dir):
    """A ``copier`` that scaffolds a minimal Doodba layout in the cwd."""
    copier = bin_dir / "copier"
    copier.write_text(
        "#!/bin/sh\n"
        'echo "$*" >> "$PWD/copier-calls.log"\n'
        "mkdir -p odoo/custom/src\n"
        "printf 'FROM ghcr.io/tecnativa/doodba:19.0-onbuild\\n' > odoo/Dockerfile\n"
        "printf 'services:\\n  odoo:\\n    build:\\n      context: odoo\\n"
        "  db:\\n    image: postgres:16\\n' > devel.yaml\n"
        "printf 'odoo_version: \"19.0\"\\n' > .copier-answers.yml\n"
    )
    copier.chmod(copier.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return copier


def test_init_doodba_writes_docker_conventions(
    doodba_project, fake_docker, monkeypatch
):
    """``osh init doodba`` records Doodba's service conventions."""
    monkeypatch.chdir(doodba_project)

    result = CliRunner().invoke(main, ["init", "doodba", "19.0", "--yes"])

    assert result.exit_code == 0, result.output
    docker_toml = (doodba_project / ".osh" / "docker.toml").read_text()
    assert "service = 'odoo'" in docker_toml
    assert "command = 'odoo'" in docker_toml
    assert "db_service = 'db'" in docker_toml
    assert "compose_file = 'docker-compose.yml'" in docker_toml


def test_init_doodba_prepares_the_layout(doodba_project, fake_docker, monkeypatch):
    """Init creates ``odoo/auto/addons`` and the compose symlink."""
    monkeypatch.chdir(doodba_project)

    result = CliRunner().invoke(main, ["init", "doodba", "19.0", "--yes"])

    assert result.exit_code == 0, result.output
    auto = doodba_project / "odoo" / "auto" / "addons"
    assert auto.is_dir()
    link = doodba_project / "docker-compose.yml"
    assert link.is_symlink()
    assert link.resolve() == (doodba_project / "devel.yaml").resolve()


def test_init_doodba_builds_then_aggregates(doodba_project, fake_docker, monkeypatch):
    """A fresh project builds the image and runs the devel-setup aggregation."""
    monkeypatch.chdir(doodba_project)

    result = CliRunner().invoke(main, ["init", "doodba", "19.0", "--yes"])

    assert result.exit_code == 0, result.output
    calls = _calls(fake_docker)
    build = next(i for i, c in enumerate(calls) if c.endswith(" build"))
    setup = next(i for i, c in enumerate(calls) if "run --rm -T devel-setup" in c)
    assert build < setup


def test_init_doodba_skips_aggregation_when_src_populated(
    doodba_project, fake_docker, monkeypatch
):
    """An aggregated checkout is not re-aggregated on re-init."""
    (doodba_project / "odoo" / "custom" / "src" / "odoo").mkdir(parents=True)
    monkeypatch.chdir(doodba_project)

    result = CliRunner().invoke(main, ["init", "doodba", "19.0", "--yes"])

    assert result.exit_code == 0, result.output
    assert not any("devel-setup" in c for c in _calls(fake_docker))


def test_init_doodba_compose_file_option(doodba_project, fake_docker, monkeypatch):
    """``--compose-file`` wins over the detected dev files."""
    (doodba_project / "docker-compose.yml").write_text("services:\n  odoo:\n")
    (doodba_project / "prod.yaml").write_text("services:\n  odoo:\n")
    monkeypatch.chdir(doodba_project)

    result = CliRunner().invoke(
        main,
        ["init", "doodba", "19.0", "--compose-file", "prod.yaml", "--yes"],
    )

    assert result.exit_code == 0, result.output
    docker_toml = (doodba_project / ".osh" / "docker.toml").read_text()
    assert "compose_file = 'prod.yaml'" in docker_toml


def test_init_doodba_missing_layout_needs_copier(tmp_project, fake_docker, monkeypatch):
    """A non-Doodba directory without copier fails with a scaffold hint."""
    monkeypatch.chdir(tmp_project)
    monkeypatch.setattr(
        "osh.plugins.osh_runtime_doodba.utils.shutil.which", lambda _name: None
    )

    result = CliRunner().invoke(main, ["init", "doodba", "19.0", "--yes"])

    assert result.exit_code != 0
    assert "Not a Doodba project" in result.output
    assert "copier" in result.output
    assert not (tmp_project / ".osh" / "docker.toml").exists()


def test_init_doodba_scaffolds_with_copier(
    tmp_project, fake_docker, monkeypatch, tmp_path
):
    """With copier on PATH, ``--yes`` scaffolds the Doodba layout."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_fake_copier(bin_dir)
    monkeypatch.setenv(
        "PATH",
        f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
    )
    monkeypatch.chdir(tmp_project)

    result = CliRunner().invoke(main, ["init", "doodba", "19.0", "--yes"])

    assert result.exit_code == 0, result.output
    copier_call = (tmp_project / "copier-calls.log").read_text()
    assert "--defaults" in copier_call
    assert "odoo_version=19.0" in copier_call
    assert (tmp_project / ".osh" / "docker.toml").exists()
    assert (tmp_project / "odoo" / "auto" / "addons").is_dir()


def test_init_doodba_dry_run_writes_nothing(doodba_project, fake_docker, monkeypatch):
    """``--dry-run`` reports without touching the project."""
    monkeypatch.chdir(doodba_project)

    result = CliRunner().invoke(main, ["init", "doodba", "19.0", "--dry-run", "--yes"])

    assert result.exit_code == 0, result.output
    assert not (doodba_project / ".osh" / "docker.toml").exists()
    assert not (doodba_project / "odoo" / "auto").exists()
    assert not (doodba_project / "docker-compose.yml").exists()
    mutating = ("build", "run", "up", "down", "create")
    assert not any(
        f" {verb} " in f" {call} " for call in _calls(fake_docker) for verb in mutating
    )


def test_init_doodba_writes_the_odoo_seed_conf(
    doodba_project, fake_docker, monkeypatch
):
    """The base conf seeds proxy/smtp defaults for generated configs."""
    monkeypatch.chdir(doodba_project)

    result = CliRunner().invoke(main, ["init", "doodba", "19.0", "--yes"])

    assert result.exit_code == 0, result.output
    seed = (doodba_project / ".osh" / "doodba-odoo.conf").read_text()
    assert "proxy_mode = True" in seed
    assert "smtp_server = smtp" in seed


def test_doodba_detects_version_from_build_args(
    doodba_project, doodba_config_json, monkeypatch
):
    """The Odoo version comes from the compose ``ODOO_VERSION`` build arg."""
    assert DoodbaRuntime().detect_odoo_version(doodba_project) == "odoo 19.0"


def test_doodba_detects_version_from_copier_answers(tmp_project, fake_docker):
    """``.copier-answers.yml`` is the fallback version source."""
    (tmp_project / ".copier-answers.yml").write_text("odoo_version: '18.0'\n")

    assert DoodbaRuntime().detect_odoo_version(tmp_project) == "odoo 18.0"


def test_doodba_odoo_port_comes_from_the_proxy(
    doodba_project, doodba_config_json, monkeypatch
):
    """The published port is odoo_proxy's ``<major>069`` mapping."""
    assert DoodbaRuntime().odoo_port(doodba_project) == 19069


def test_doodba_odoo_port_prefers_docker_toml(doodba_project, fake_docker, monkeypatch):
    """A configured ``port`` wins over the proxy's published one."""
    (doodba_project / ".osh" / "docker.toml").write_text(
        "service = 'odoo'\nport = 9999\n"
    )

    assert DoodbaRuntime().odoo_port(doodba_project) == 9999


def test_doodba_addons_paths_flattened_dir_first(doodba_project, fake_docker):
    """``/opt/odoo/auto/addons`` leads; project ``odoo/`` paths are covered."""
    paths = DoodbaRuntime().build_addons_paths(doodba_project)

    assert paths[0].as_posix() == "/opt/odoo/auto/addons"
    assert not any(str(path).startswith("/mnt/extra-addons/odoo/") for path in paths)


def test_doodba_help_marks_the_runtime_experimental():
    """The experimental status is visible in init help and runtime meta."""
    result = CliRunner().invoke(main, ["init", "doodba", "--help"])

    assert result.exit_code == 0, result.output
    assert "Experimental" in result.output

    from osh.utils.plugin_loader import runtime_meta

    assert "(experimental)" in runtime_meta()["doodba"]


def test_stop_volumes_downs_the_stack(doodba_project, fake_docker, monkeypatch):
    """``osh stop --volumes`` passes ``--volumes`` to ``compose down``."""
    set_project_config(doodba_project, "run", "runtime", "doodba")
    (doodba_project / ".osh" / "docker.toml").write_text(
        "service = 'odoo'\ncompose_file = 'devel.yaml'\n"
    )
    monkeypatch.chdir(doodba_project)

    result = CliRunner().invoke(main, ["stop", "--volumes"])

    assert result.exit_code == 0, result.output
    assert any(
        c.endswith("down --volumes") or "down --volumes" in c
        for c in _calls(fake_docker)
    )


def test_doodba_docker_toml_conventions_cover_build_inputs(
    doodba_project, fake_docker, monkeypatch
):
    """The build fingerprint watches Doodba's build-time inputs."""
    monkeypatch.chdir(doodba_project)

    result = CliRunner().invoke(main, ["init", "doodba", "19.0", "--yes"])

    assert result.exit_code == 0, result.output
    docker_toml = (doodba_project / ".osh" / "docker.toml").read_text()
    assert "build_inputs" in docker_toml
    assert "custom/build.d" in docker_toml
