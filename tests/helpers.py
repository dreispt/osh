"""Helper functions shared by ``tests/`` and plugin test directories."""

import os
import subprocess
from pathlib import Path

PLUGINS_DATA = Path(__file__).parent / "plugins"


def write_stub_pip(venv_path):
    """Drop a no-op ``pip`` executable into *venv_path*.

    ``pip install`` is the one step of ``osh venv init`` that cannot run
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
