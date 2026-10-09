"""Helper functions shared by ``tests/`` and plugin test directories."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

PLUGINS_DATA = Path(__file__).parent / "plugins"


def write_stub_pip(venv_path):
    """Drop a no-op ``pip`` executable into *venv_path*.

    ``pip install`` is the one step of ``osh init --runtime=venv`` that cannot run
    for real (it fetches from PyPI), so a stub is provided as a real
    executable file — the same canned-answer technique as
    ``osh_runtime_docker/tests/fakebin/docker``.
    """
    bin_dir = Path(venv_path) / ("Scripts" if os.name == "nt" else "bin")
    bin_dir.mkdir(parents=True, exist_ok=True)
    pip = bin_dir / "pip"
    pip.write_text("#!/bin/sh\nexit 0\n")
    pip.chmod(0o755)


def make_bare_repo(tmp_path, name, branches=("master",)):
    """Create a bare git repository with commits on the requested branches."""
    repo = tmp_path / name
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    (repo / "README").write_text(name)
    subprocess.run(["git", "add", "README"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    for branch in branches[1:]:
        subprocess.run(
            ["git", "checkout", "-b", branch],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / branch).write_text(branch)
        subprocess.run(
            ["git", "add", branch],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", branch],
            cwd=repo,
            check=True,
            capture_output=True,
        )
    subprocess.run(
        ["git", "checkout", branches[0]],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    bare = tmp_path / f"{name}.git"
    subprocess.run(
        ["git", "clone", "--bare", str(repo), str(bare)],
        check=True,
        capture_output=True,
    )
    return bare


# Real processes and ports -------------------------------------------------

_LISTENER = """\
import signal
import socket
import sys
import time

if "--ignore-term" in sys.argv:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
sock = socket.socket()
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("0.0.0.0", int(sys.argv[1])))
sock.listen(1)
time.sleep(3600)
"""

_SLEEPER = "import time; time.sleep(3600)"


def _free_port():
    """Return a TCP port that is free right now."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _configure_port(project, port):
    """Point the project's Odoo HTTP port at *port*."""
    conf = project / ".osh" / "odoo.conf"
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text(f"[options]\nhttp_port = {port}\n")


def _wait_listening(port, timeout=10):
    """Wait until *port* accepts a connection."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"nothing listening on port {port}")


def _odoo_argv(tmp_path, port, name="odoo", ignore_term=False):
    """Return the argv of a listener script whose cmdline looks like Odoo's."""
    script = tmp_path / name
    script.write_text(f"#!{sys.executable}\n{_LISTENER}")
    script.chmod(0o755)
    argv = [str(script), str(port)]
    if ignore_term:
        argv.append("--ignore-term")
    return argv


def _odoo_module_argv(tmp_path, port):
    """Return the ``python -m odoo`` argv of a real listener, plus its env."""
    package = tmp_path / "odoo"
    package.mkdir()
    (package / "__main__.py").write_text(_LISTENER)
    return (
        [sys.executable, "-m", "odoo", str(port)],
        {**os.environ, "PYTHONPATH": str(tmp_path)},
    )


def _sleeping_odoo(tmp_path):
    """Spawn a real process looking like a leftover Osh-managed Odoo."""
    script = tmp_path / "odoo"
    script.write_text(f"#!{sys.executable}\n{_SLEEPER}")
    script.chmod(0o755)
    conf = tmp_path / ".osh" / "odoo.conf"
    conf.parent.mkdir(exist_ok=True)
    conf.write_text("")
    return subprocess.Popen(
        [str(script)],
        env={**os.environ, "ODOO_RC": str(conf)},
        close_fds=True,
    )
