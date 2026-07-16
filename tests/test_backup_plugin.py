"""Tests for the `osh backup` plugin."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from urllib.request import Request

import pytest
from click.testing import CliRunner

from osh.plugins.osh_backup.commands import backup
from osh.plugins.osh_backup.sources import SourceError, SshSource


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Return a minimal Osh project directory."""
    project = tmp_path / "project"
    project.mkdir()
    (project / ".osh").mkdir()
    return project


@pytest.fixture
def in_project(monkeypatch, tmp_project: Path) -> Path:
    """Make _find_project_root return the temporary project."""
    monkeypatch.setattr("osh.utils._find_project_root", lambda: tmp_project)
    monkeypatch.setattr(
        "osh.plugins.osh_backup.cache._find_project_root", lambda: tmp_project
    )
    monkeypatch.chdir(tmp_project)
    return tmp_project


def test_download_db_source_writes_to_cache(in_project: Path, monkeypatch) -> None:
    """Downloading a db:// source writes the dump and metadata into the cache."""
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(args)
        # Simulate pg_dump writing to stdout.
        if "stdout" in kwargs and kwargs["stdout"] is not None:
            kwargs["stdout"].write(b"pg_dump output")
        return subprocess.CompletedProcess(args, returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    runner = CliRunner()
    result = runner.invoke(backup, ["download", "db://sourcedb"])

    assert result.exit_code == 0
    cache_dir = in_project / ".osh" / "backups"
    files = list(cache_dir.iterdir())
    dump_files = [p for p in files if not p.name.endswith(".meta.json")]
    assert len(dump_files) == 1
    assert dump_files[0].read_bytes() == b"pg_dump output"

    meta_path = Path(str(dump_files[0]) + ".meta.json")
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text())
    assert meta["source"] == "db://sourcedb"
    assert meta["format"] == "dump"


def test_download_requires_output_outside_project(monkeypatch, tmp_path: Path) -> None:
    """Outside a project, `backup download` requires --output."""
    monkeypatch.setattr("osh.utils._find_project_root", lambda: None)

    runner = CliRunner()
    result = runner.invoke(backup, ["download", "db://sourcedb"])

    assert result.exit_code != 0
    assert "--output PATH" in result.output


def test_download_with_output_outside_project(monkeypatch, tmp_path: Path) -> None:
    """With --output, `backup download` works outside a project."""
    monkeypatch.setattr("osh.utils._find_project_root", lambda: None)
    output = tmp_path / "sourcedb.dump"

    def fake_run(args, **kwargs):
        if "stdout" in kwargs and kwargs["stdout"] is not None:
            kwargs["stdout"].write(b"dump")
        return subprocess.CompletedProcess(args, returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    runner = CliRunner()
    result = runner.invoke(backup, ["download", "db://sourcedb", str(output)])

    assert result.exit_code == 0
    assert output.exists()
    assert output.read_bytes() == b"dump"
    assert not Path(str(output) + ".meta.json").exists()


def test_download_https_posts_payload(in_project: Path, monkeypatch) -> None:
    """The HTTPS source POSTs the expected payload and streams the response."""
    requests: list[Request] = []

    class FakeResponse:
        def __init__(self) -> None:
            self._data = b"zip content"

        def read(self, size: int = -1) -> bytes:
            data, self._data = self._data, b""
            return data

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

    def fake_urlopen(req, **kwargs):
        requests.append(req)
        return FakeResponse()

    monkeypatch.setattr("osh.plugins.osh_backup.sources.urlopen", fake_urlopen)

    runner = CliRunner()
    result = runner.invoke(
        backup,
        [
            "download",
            "https://demo.odoo.com?db=prod&format=zip",
            "--master-password",
            "secret",
        ],
    )

    assert result.exit_code == 0
    assert len(requests) == 1
    req = requests[0]
    assert req.full_url == "https://demo.odoo.com/web/database/backup"
    payload = req.data.decode()
    assert "master_pwd=secret" in payload
    assert "name=prod" in payload
    assert "backup_format=zip" in payload

    cache_dir = in_project / ".osh" / "backups"
    zip_file = next(cache_dir.glob("*.zip"))
    assert zip_file.read_bytes() == b"zip content"


def test_download_odoosh_dry_run(in_project: Path) -> None:
    """odoosh:// dry-run prints the expected ssh and scp commands."""
    runner = CliRunner()
    result = runner.invoke(
        backup,
        [
            "download",
            "odoosh://123456@my-project-master-123456.dev.odoo.com",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert "ssh" in result.output
    assert "scp" in result.output
    assert "123456@my-project-master-123456.dev.odoo.com" in result.output


def test_list_cached_backups(in_project: Path) -> None:
    """`osh backup list` shows cached backups newest first."""
    cache_dir = in_project / ".osh" / "backups"
    cache_dir.mkdir(parents=True, exist_ok=True)
    first = cache_dir / "first.dump"
    second = cache_dir / "second.zip"
    first.write_bytes(b"x")
    second.write_bytes(b"y")
    Path(str(first) + ".meta.json").write_text(
        json.dumps({"source": "db://db1", "format": "dump", "created_at": "2026-01-01"})
    )
    Path(str(second) + ".meta.json").write_text(
        json.dumps(
            {
                "source": "https://host?db=prod",
                "format": "zip",
                "created_at": "2026-01-02",
            }
        )
    )

    runner = CliRunner()
    result = runner.invoke(backup, ["list"])

    assert result.exit_code == 0
    assert "second.zip" in result.output
    assert "first.dump" in result.output
    assert "https://host?db=prod" in result.output


def test_list_outside_project(monkeypatch, tmp_path: Path) -> None:
    """`osh backup list` fails outside an Osh project."""
    monkeypatch.setattr("osh.utils._find_project_root", lambda: None)

    runner = CliRunner()
    result = runner.invoke(backup, ["list"])

    assert result.exit_code != 0
    assert "Not inside an Osh project" in result.output


def test_ssh_source_parses_url_components() -> None:
    """An ssh:// source extracts user, host, port and remote path."""
    source = SshSource("ssh://admin@myhost:2222/var/backups/odoo.sql.gz")

    assert source.username == "admin"
    assert source.host == "myhost"
    assert source.port == 2222
    assert source.path == "/var/backups/odoo.sql.gz"
    assert source.original_format == "sql.gz"


def test_ssh_source_default_output_name_uses_host_and_file() -> None:
    """The default output name contains the host and remote filename."""
    source = SshSource("ssh://myhost/var/backups/odoo.sql.gz")

    name = source.default_output_name()
    assert name.startswith("myhost_odoo.sql.gz_")
    assert name.endswith(".sql.gz")


def test_ssh_source_dry_run_shows_scp_command(capsys) -> None:
    """A dry run prints the scp command that would be run."""
    source = SshSource("ssh://user@myhost/var/backups/odoo.sql.gz")

    source.fetch(Path("/tmp/ignored"), dry_run=True)
    captured = capsys.readouterr()

    assert "Would run: scp" in captured.err
    assert "user@myhost:/var/backups/odoo.sql.gz" in captured.err


def test_ssh_source_runs_scp(monkeypatch, tmp_path: Path) -> None:
    """Fetching an ssh:// source runs scp with the expected arguments."""
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(args)
        # scp writes to the local destination (last argument) itself.
        Path(args[-1]).write_bytes(b"backup data")
        return subprocess.CompletedProcess(args, returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    ssh_key = tmp_path / "id_rsa"
    ssh_key.write_text("key")
    source = SshSource("ssh://user@myhost/var/backups/odoo.sql.gz", ssh_key=ssh_key)
    output = tmp_path / "dump.sql.gz"

    source.fetch(output)

    assert output.read_bytes() == b"backup data"
    assert calls[0][:4] == [
        "scp",
        "-i",
        str(ssh_key),
        "user@myhost:/var/backups/odoo.sql.gz",
    ]
    assert calls[0][-1] == str(output)


def test_ssh_source_with_port_includes_p_flag(monkeypatch, tmp_path: Path) -> None:
    """A non-standard SSH port is passed to scp with -P."""
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    output = tmp_path / "dump.sql.gz"
    source = SshSource("ssh://user@myhost:2222/var/backups/odoo.sql.gz")
    source.fetch(output)

    assert "-P" in calls[0]
    assert "2222" in calls[0]


def test_ssh_source_missing_host_or_path_raises() -> None:
    """An ssh:// source without host or path is rejected."""
    with pytest.raises(SourceError):
        SshSource("ssh:///var/backups/odoo.sql.gz")


def test_download_ssh_source_invokes_fetch(monkeypatch, tmp_path: Path) -> None:
    """`osh backup download ssh://...` copies the remote file into the cache."""
    project = tmp_path / "project"
    project.mkdir()
    (project / ".osh").mkdir()
    monkeypatch.setattr("osh.utils._find_project_root", lambda: project)
    monkeypatch.chdir(project)

    def fake_run(args, **kwargs):
        # scp writes to the local destination (last argument) itself.
        Path(args[-1]).write_bytes(b"backup data")
        return subprocess.CompletedProcess(args, returncode=0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    runner = CliRunner()
    result = runner.invoke(
        backup, ["download", "ssh://user@myhost/var/backups/odoo.sql.gz"]
    )

    assert result.exit_code == 0
    cache_dir = project / ".osh" / "backups"
    files = [p for p in cache_dir.iterdir() if not p.name.endswith(".meta.json")]
    assert len(files) == 1
    assert files[0].read_bytes() == b"backup data"
