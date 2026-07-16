"""Tests for ``osh init`` source resolution."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from osh.commands.init_cmd import (
    DEFAULT_ODOO_URL,
    _cache_has_branch,
    _ensure_cache,
    _ensure_source,
    _find_local_enterprise_sources,
    _find_local_odoo_sources,
    _is_git_url,
    init,
)


def _make_bare_repo(
    tmp_path: Path, name: str, branches: tuple[str, ...] = ("master",)
) -> Path:
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


class TestFindLocalSources:
    def test_find_odoo_in_root(self, tmp_project: Path) -> None:
        (tmp_project / "odoo-bin").touch()
        assert _find_local_odoo_sources(tmp_project) == tmp_project.resolve()

    def test_find_odoo_in_subdirectory(self, tmp_project: Path) -> None:
        sub = tmp_project / "odoo"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "odoo-bin").touch()
        assert _find_local_odoo_sources(tmp_project) == sub.resolve()

    def test_find_enterprise_with_manifest(self, tmp_project: Path) -> None:
        ent = tmp_project / "enterprise"
        web = ent / "web"
        web.mkdir(parents=True, exist_ok=True)
        (web / "__manifest__.py").touch()
        assert _find_local_enterprise_sources(tmp_project) == ent.resolve()


class TestIsGitUrl:
    @pytest.mark.parametrize(
        ("spec", "expected"),
        [
            ("https://github.com/odoo/odoo.git", True),
            ("git@github.com:odoo/enterprise.git", True),
            ("file:///path/to/repo.git", True),
            ("ssh://git@github.com/odoo/odoo.git", True),
            ("/path/to/odoo", False),
            ("../odoo", False),
            ("C:\\odoo", False),
        ],
    )
    def test_is_git_url(self, spec: str, expected: bool) -> None:
        assert _is_git_url(spec) is expected


class TestEnsureCache:
    def test_creates_mirror(self, tmp_path: Path, patch_cache: Path) -> None:
        bare = _make_bare_repo(tmp_path, "odoo")
        cache = _ensure_cache("odoo", "master", f"file://{bare}")

        assert cache == patch_cache / "odoo.git"
        assert cache.exists()
        assert (cache / "config").exists()
        assert _cache_has_branch(cache, "master")

    def test_fetches_missing_version(self, tmp_path: Path, patch_cache: Path) -> None:
        bare = _make_bare_repo(tmp_path, "odoo", ("master", "19.0"))
        _ensure_cache("odoo", "master", f"file://{bare}")
        cache = _ensure_cache("odoo", "19.0", f"file://{bare}")

        assert _cache_has_branch(cache, "19.0")


class TestEnsureSource:
    def test_uses_project_source(self, tmp_project: Path, patch_cache: Path) -> None:
        src = tmp_project / "odoo"
        src.mkdir(parents=True, exist_ok=True)
        (src / "odoo-bin").touch()
        osh_dir = tmp_project / ".osh"
        osh_dir.mkdir(parents=True, exist_ok=True)

        result = _ensure_source(
            "odoo",
            "19.0",
            None,
            _find_local_odoo_sources(tmp_project),
            osh_dir,
            DEFAULT_ODOO_URL,
        )

        assert result == osh_dir / "odoo"
        assert (osh_dir / "odoo").is_symlink()
        assert (osh_dir / "odoo").resolve() == src.resolve()

    def test_uses_explicit_local_source(
        self, tmp_project: Path, patch_cache: Path
    ) -> None:
        src = tmp_project / "my-odoo"
        src.mkdir(parents=True, exist_ok=True)
        (src / "odoo-bin").touch()
        osh_dir = tmp_project / ".osh"
        osh_dir.mkdir(parents=True, exist_ok=True)

        result = _ensure_source(
            "odoo",
            "19.0",
            str(src),
            None,
            osh_dir,
            DEFAULT_ODOO_URL,
        )

        assert result == osh_dir / "odoo"
        assert (osh_dir / "odoo").is_symlink()
        assert (osh_dir / "odoo").resolve() == src.resolve()

    def test_clones_explicit_git_url(
        self, tmp_path: Path, tmp_project: Path, patch_cache: Path
    ) -> None:
        bare = _make_bare_repo(tmp_path, "odoo")
        osh_dir = tmp_project / ".osh"
        osh_dir.mkdir(parents=True, exist_ok=True)

        result = _ensure_source(
            "odoo",
            "master",
            f"file://{bare}",
            None,
            osh_dir,
            DEFAULT_ODOO_URL,
        )

        assert result == osh_dir / "odoo"
        assert (osh_dir / "odoo" / ".git").is_dir()
        assert (osh_dir / "odoo" / "README").exists()

    def test_clones_from_cache(
        self, tmp_path: Path, tmp_project: Path, patch_cache: Path
    ) -> None:
        bare = _make_bare_repo(tmp_path, "odoo")
        osh_dir = tmp_project / ".osh"
        osh_dir.mkdir(parents=True, exist_ok=True)

        result = _ensure_source(
            "odoo",
            "master",
            None,
            None,
            osh_dir,
            f"file://{bare}",
        )

        cache = patch_cache / "odoo.git"
        assert cache.exists()
        assert result == osh_dir / "odoo"
        assert (osh_dir / "odoo" / ".git").is_dir()
        assert (osh_dir / "odoo" / "README").exists()

    def test_existing_link_takes_precedence(
        self, tmp_project: Path, patch_cache: Path
    ) -> None:
        existing = tmp_project / "odoo"
        existing.mkdir(parents=True, exist_ok=True)
        osh_dir = tmp_project / ".osh"
        osh_dir.mkdir(parents=True, exist_ok=True)
        (osh_dir / "odoo").symlink_to(existing)

        result = _ensure_source(
            "odoo",
            "19.0",
            "https://example.com/odoo.git",
            None,
            osh_dir,
            DEFAULT_ODOO_URL,
        )

        assert result == osh_dir / "odoo"
        assert (osh_dir / "odoo").resolve() == existing.resolve()

    def test_skip_when_user_declines(
        self, tmp_project: Path, patch_cache: Path, monkeypatch
    ) -> None:
        osh_dir = tmp_project / ".osh"
        osh_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr("osh.commands.init_cmd.sys.stdin.isatty", lambda: True)
        monkeypatch.setattr(
            "osh.commands.init_cmd.click.confirm", lambda *a, **kw: False
        )
        monkeypatch.setattr("osh.commands.init_cmd.click.prompt", lambda *a, **kw: "")

        result = _ensure_source(
            "odoo",
            "19.0",
            None,
            None,
            osh_dir,
            DEFAULT_ODOO_URL,
        )

        assert result is None
        assert not (osh_dir / "odoo").exists()


class TestInitCommand:
    def _real_git_only_subprocess(self, monkeypatch) -> list:
        """Run git commands for real; record/no-op everything else."""
        calls: list = []
        real_check_call = subprocess.check_call

        def fake_check_call(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args")
            if isinstance(cmd, (list, tuple)) and "git" in cmd:
                return real_check_call(*args, **kwargs)
            calls.append(cmd)
            return None

        monkeypatch.setattr(
            "osh.commands.init_cmd.subprocess.check_call", fake_check_call
        )
        monkeypatch.setattr("venv.create", lambda *a, **kw: None)
        return calls

    def test_with_project_sources(self, tmp_project: Path, monkeypatch) -> None:
        odoo_src = tmp_project / "odoo"
        odoo_src.mkdir(parents=True, exist_ok=True)
        (odoo_src / "odoo-bin").touch()
        web = tmp_project / "enterprise" / "web"
        web.mkdir(parents=True, exist_ok=True)
        (web / "__manifest__.py").touch()

        self._real_git_only_subprocess(monkeypatch)
        runner = CliRunner()
        result = runner.invoke(init, ["19.0", str(tmp_project)])

        assert result.exit_code == 0
        assert (tmp_project / ".osh" / "odoo").is_symlink()
        assert (tmp_project / ".osh" / "enterprise").is_symlink()
        assert (tmp_project / ".osh" / "config").exists()

    def test_with_source_flags(self, tmp_project: Path, monkeypatch) -> None:
        odoo_src = tmp_project / "my-odoo"
        odoo_src.mkdir(parents=True, exist_ok=True)
        (odoo_src / "odoo-bin").touch()
        ent_src = tmp_project / "my-ent"
        web = ent_src / "web"
        web.mkdir(parents=True, exist_ok=True)
        (web / "__manifest__.py").touch()

        self._real_git_only_subprocess(monkeypatch)
        runner = CliRunner()
        result = runner.invoke(
            init,
            [
                "19.0",
                str(tmp_project),
                "-c",
                str(odoo_src),
                "-e",
                str(ent_src),
            ],
        )

        assert result.exit_code == 0
        assert (tmp_project / ".osh" / "odoo").resolve() == odoo_src.resolve()
        assert (tmp_project / ".osh" / "enterprise").resolve() == ent_src.resolve()

    def test_cache_first_non_interactive(
        self,
        tmp_path: Path,
        tmp_project: Path,
        patch_cache: Path,
        monkeypatch,
    ) -> None:
        odoo_bare = _make_bare_repo(tmp_path, "odoo")
        ent_bare = _make_bare_repo(tmp_path, "enterprise")
        monkeypatch.setattr(
            "osh.commands.init_cmd.DEFAULT_ODOO_URL", f"file://{odoo_bare}"
        )
        monkeypatch.setattr(
            "osh.commands.init_cmd.DEFAULT_ENTERPRISE_URL", f"file://{ent_bare}"
        )
        self._real_git_only_subprocess(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(init, ["master", str(tmp_project)])

        assert result.exit_code == 0
        assert (patch_cache / "odoo.git").exists()
        assert (patch_cache / "enterprise.git").exists()
        assert (tmp_project / ".osh" / "odoo" / ".git").is_dir()
        assert (tmp_project / ".osh" / "enterprise" / ".git").is_dir()

    def test_pip_install_failure_still_initializes(
        self, tmp_project: Path, monkeypatch
    ) -> None:
        """If pip install fails, the project environment is still created."""
        odoo_src = tmp_project / "odoo"
        odoo_src.mkdir(parents=True, exist_ok=True)
        (odoo_src / "odoo-bin").touch()
        (odoo_src / "requirements.txt").touch()
        ent_src = tmp_project / "enterprise"
        web = ent_src / "web"
        web.mkdir(parents=True, exist_ok=True)
        (web / "__manifest__.py").touch()

        monkeypatch.setattr("venv.create", lambda *a, **kw: None)

        def failing_check_call(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args")
            if isinstance(cmd, (list, tuple)) and "git" in cmd:
                return subprocess.check_call(*args, **kwargs)
            raise subprocess.CalledProcessError(1, cmd)

        monkeypatch.setattr(
            "osh.commands.init_cmd.subprocess.check_call", failing_check_call
        )

        runner = CliRunner()
        result = runner.invoke(
            init, ["19.0", str(tmp_project), "-c", str(odoo_src), "-e", str(ent_src)]
        )

        assert result.exit_code == 0
        assert (tmp_project / ".osh" / "odoo").is_symlink()
        assert (tmp_project / ".osh" / "enterprise").is_symlink()
        assert (tmp_project / ".osh" / "config").exists()
        assert "pip install failed" in result.output

    def test_smoke_test_succeeds_when_odoo_executable_works(
        self, tmp_project: Path, monkeypatch
    ) -> None:
        """After pip install, a working Odoo executable passes the smoke test."""
        odoo_src = tmp_project / "odoo"
        odoo_src.mkdir(parents=True, exist_ok=True)
        (odoo_src / "odoo-bin").touch()

        venv_bin = tmp_project / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        odoo_exe = venv_bin / "odoo"
        odoo_exe.write_text("#!/bin/sh\necho Odoo 19.0")
        odoo_exe.chmod(0o755)

        self._real_git_only_subprocess(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(init, ["19.0", str(tmp_project), "-c", str(odoo_src)])

        assert result.exit_code == 0
        assert "Running quick Odoo smoke test" in result.output
        assert "Initialised project directory" in result.output
        assert "Odoo setup incomplete" not in result.output

    def test_smoke_test_failure_still_initializes(
        self, tmp_project: Path, monkeypatch
    ) -> None:
        """A failing smoke test keeps the project initialised and warns."""
        odoo_src = tmp_project / "odoo"
        odoo_src.mkdir(parents=True, exist_ok=True)
        (odoo_src / "odoo-bin").touch()

        venv_bin = tmp_project / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        odoo_exe = venv_bin / "odoo"
        odoo_exe.write_text("#!/bin/sh\necho error; exit 1")
        odoo_exe.chmod(0o755)

        self._real_git_only_subprocess(monkeypatch)

        runner = CliRunner()
        result = runner.invoke(init, ["19.0", str(tmp_project), "-c", str(odoo_src)])

        assert result.exit_code == 0
        assert "Warning: Odoo smoke test failed" in result.output
        assert (tmp_project / ".osh" / "odoo").is_symlink()
        assert (tmp_project / ".osh" / "config").exists()
