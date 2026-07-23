"""Tests for the `osh backup` command."""

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from osh.backup_sources import OdooshSource, SourceError, SshSource
from osh.commands.backup_cmd import backup


def test_download_db_source_writes_to_cache(in_project, subprocess_run_capture):
    """Downloading a db:// source writes the dump and metadata into the cache."""
    subprocess_run_capture.stdout = b"pg_dump output"

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


def test_download_requires_output_outside_project(monkeypatch, tmp_path):
    """Outside a project, `backup download` requires --output."""
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(backup, ["download", "db://sourcedb"])

    assert result.exit_code != 0
    assert "--output PATH" in result.output


def test_download_with_output_outside_project(
    monkeypatch, tmp_path, subprocess_run_capture
):
    """With --output, `backup download` works outside a project."""
    monkeypatch.chdir(tmp_path)
    output = tmp_path / "sourcedb.dump"

    subprocess_run_capture.stdout = b"dump"

    runner = CliRunner()
    result = runner.invoke(backup, ["download", "db://sourcedb", str(output)])

    assert result.exit_code == 0
    assert output.exists()
    assert output.read_bytes() == b"dump"
    assert not Path(str(output) + ".meta.json").exists()


def test_download_https_posts_payload(in_project, monkeypatch):
    """The HTTPS source POSTs the expected payload and streams the response."""
    requests = []

    class FakeResponse:
        def __init__(self):
            self._data = b"zip content"

        def read(self, size=-1):
            data, self._data = self._data, b""
            return data

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

    def fake_urlopen(req, **kwargs):
        requests.append(req)
        return FakeResponse()

    monkeypatch.setattr("osh.backup_sources.urlopen", fake_urlopen)

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


def test_download_odoosh_dry_run(in_project):
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


def test_download_odoosh_dry_run_without_build_id(in_project):
    """odoosh:// dry-run infers the build id from the domain suffix."""
    runner = CliRunner()
    result = runner.invoke(
        backup,
        [
            "download",
            "odoosh://my-project-master-123456.dev.odoo.com",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert "ssh" in result.output
    assert "scp" in result.output
    assert "123456@my-project-master-123456.dev.odoo.com" in result.output


def test_odoosh_source_extracts_build_id_from_domain():
    """When the username is omitted, the build id is parsed from the domain."""
    source = OdooshSource("odoosh://my-project-master-123456.dev.odoo.com")

    assert source.build_id == "123456"
    assert source.domain == "my-project-master-123456.dev.odoo.com"
    assert source.ssh_target == "123456@my-project-master-123456.dev.odoo.com"


def test_odoosh_source_expands_shorthand_domain():
    """A slug without the .dev.odoo.com suffix is expanded automatically."""
    source = OdooshSource("odoosh://osi-sh-barberhood-main-29869268")

    assert source.build_id == "29869268"
    assert source.domain == "osi-sh-barberhood-main-29869268.dev.odoo.com"
    assert source.ssh_target == "29869268@osi-sh-barberhood-main-29869268.dev.odoo.com"


def test_odoosh_source_prefers_explicit_build_id():
    """An explicit username overrides the build id parsed from the domain."""
    source = OdooshSource("odoosh://999999@my-project-master-123456.dev.odoo.com")

    assert source.build_id == "999999"


def test_odoosh_source_parses_db_name_from_backup_filename():
    """The database name is extracted from the daily backup filename."""
    source = OdooshSource(
        "odoosh://my-project-master-123456.dev.odoo.com"
        "?backup=2023-09-07_010937-my-project-prod-123456_daily.sql.gz"
    )
    source._resolve_remote_file()

    assert source.db_name == "my-project-prod"


def test_odoosh_source_include_filestore_changes_format():
    """With include_filestore, the output becomes a .zip backup."""
    source = OdooshSource(
        "odoosh://my-project-master-123456.dev.odoo.com",
        include_filestore=True,
    )

    assert source.original_format == "zip"
    assert source.default_output_name().endswith(".zip")


def test_download_odoosh_with_filestore_dry_run(in_project):
    """--filestore dry-run reports the full backup download."""
    runner = CliRunner()
    result = runner.invoke(
        backup,
        [
            "download",
            "odoosh://my-project-master-123456.dev.odoo.com",
            "--filestore",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert "filestore" in result.output
    assert "odoosh://my-project-master-123456.dev.odoo.com" in result.output
    assert ".zip" in result.output


def test_download_odoosh_with_filestore_creates_zip(
    in_project, monkeypatch, tmp_path, subprocess_run_capture
):
    """--filestream fetches the dump and filestore and packages them as a zip."""
    import gzip
    import io
    import tarfile
    import time
    import zipfile

    source = OdooshSource(
        "odoosh://my-project-master-123456.dev.odoo.com"
        "?backup=2023-09-07_010937-my-project-prod-123456_daily.sql.gz",
        include_filestore=True,
    )

    dump_gz = tmp_path / "dump.sql.gz"
    with gzip.open(dump_gz, "wb") as f:
        f.write(b"-- dump")

    filestore_buf = io.BytesIO()
    with tarfile.open(fileobj=filestore_buf, mode="w:gz") as tar:
        data = b"attachment content"
        info = tarfile.TarInfo(name="myfile.txt")
        info.size = len(data)
        info.mtime = time.time()
        tar.addfile(info, io.BytesIO(data))
    filestore_tar = filestore_buf.getvalue()

    def fake_run(args, **kwargs):
        if args[0] == "scp":
            Path(args[-1]).write_bytes(dump_gz.read_bytes())
        return subprocess.CompletedProcess(args, returncode=0)

    subprocess_run_capture.side_effect = fake_run

    def fake_pipeline(commands, **kwargs):
        tar_cmd = commands[-1]
        filestore_dir = Path(tar_cmd[tar_cmd.index("-C") + 1])
        with tarfile.open(fileobj=io.BytesIO(filestore_tar), mode="r:gz") as tar:
            tar.extractall(filestore_dir)
        return 0, b"", ""

    monkeypatch.setattr("osh.backup_sources.run_shell_pipeline", fake_pipeline)

    output = tmp_path / "backup.zip"
    source.fetch(output)

    assert output.exists()
    with zipfile.ZipFile(output, "r") as zf:
        names = zf.namelist()
        assert "dump.sql" in names
        assert "filestore/myfile.txt" in names


def test_odoosh_source_without_build_id_raises():
    """A domain without a numeric odoo.sh build suffix is rejected."""
    with pytest.raises(SourceError):
        OdooshSource("odoosh://my-project-master.dev.odoo.com")


def test_list_cached_backups(in_project):
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


def test_list_outside_project(monkeypatch, tmp_path):
    """`osh backup list` fails outside an Osh project."""
    monkeypatch.chdir(tmp_path)

    runner = CliRunner()
    result = runner.invoke(backup, ["list"])

    assert result.exit_code == 0
    assert "Not inside an Osh project" in result.output


def test_ssh_source_parses_url_components():
    """An ssh:// source extracts user, host, port and remote path."""
    source = SshSource("ssh://admin@myhost:2222/var/backups/odoo.sql.gz")

    assert source.username == "admin"
    assert source.host == "myhost"
    assert source.port == 2222
    assert source.path == "/var/backups/odoo.sql.gz"
    assert source.original_format == "sql.gz"


def test_ssh_source_default_output_name_uses_host_and_file():
    """The default output name contains the host and remote filename."""
    source = SshSource("ssh://myhost/var/backups/odoo.sql.gz")

    name = source.default_output_name()
    assert name.startswith("myhost_odoo.sql.gz_")
    assert name.endswith(".sql.gz")


def test_ssh_source_dry_run_shows_scp_command(capsys):
    """A dry run prints the scp command that would be run."""
    source = SshSource("ssh://user@myhost/var/backups/odoo.sql.gz")

    source.fetch(Path("/tmp/ignored"), dry_run=True)
    captured = capsys.readouterr()

    assert "Would run: scp" in captured.err
    assert "user@myhost:/var/backups/odoo.sql.gz" in captured.err


def test_ssh_source_runs_scp(tmp_path, subprocess_run_capture):
    """Fetching an ssh:// source runs scp with the expected arguments."""

    def _scp_write(args, **kwargs):
        subprocess_run_capture.calls.append(list(args))
        # scp writes to the local destination (last argument) itself.
        Path(args[-1]).write_bytes(b"backup data")
        return subprocess.CompletedProcess(args, returncode=0)

    subprocess_run_capture.side_effect = _scp_write

    ssh_key = tmp_path / "id_rsa"
    ssh_key.write_text("key")
    source = SshSource("ssh://user@myhost/var/backups/odoo.sql.gz", ssh_key=ssh_key)
    output = tmp_path / "dump.sql.gz"

    source.fetch(output)

    assert output.read_bytes() == b"backup data"
    assert subprocess_run_capture.calls[0][:4] == [
        "scp",
        "-i",
        str(ssh_key),
        "user@myhost:/var/backups/odoo.sql.gz",
    ]
    assert subprocess_run_capture.calls[0][-1] == str(output)


def test_ssh_source_with_port_includes_p_flag(tmp_path, subprocess_run_capture):
    """A non-standard SSH port is passed to scp with -P."""
    output = tmp_path / "dump.sql.gz"
    source = SshSource("ssh://user@myhost:2222/var/backups/odoo.sql.gz")
    source.fetch(output)

    assert "-P" in subprocess_run_capture.calls[0]
    assert "2222" in subprocess_run_capture.calls[0]


def test_ssh_source_missing_host_or_path_raises():
    """An ssh:// source without host or path is rejected."""
    with pytest.raises(SourceError):
        SshSource("ssh:///var/backups/odoo.sql.gz")


def test_download_ssh_source_invokes_fetch(
    monkeypatch, tmp_project, subprocess_run_capture
):
    """`osh backup download ssh://...` copies the remote file into the cache."""
    monkeypatch.chdir(tmp_project)

    def _scp_write(args, **kwargs):
        # scp writes to the local destination (last argument) itself.
        Path(args[-1]).write_bytes(b"backup data")
        return subprocess.CompletedProcess(args, returncode=0)

    subprocess_run_capture.side_effect = _scp_write

    runner = CliRunner()
    result = runner.invoke(
        backup, ["download", "ssh://user@myhost/var/backups/odoo.sql.gz"]
    )

    assert result.exit_code == 0
    cache_dir = tmp_project / ".osh" / "backups"
    files = [p for p in cache_dir.iterdir() if not p.name.endswith(".meta.json")]
    assert len(files) == 1
    assert files[0].read_bytes() == b"backup data"
