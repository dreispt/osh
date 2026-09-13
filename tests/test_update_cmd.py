"""Tests for the ``osh update`` command and its fingerprint/store helpers."""

import pytest
from click.testing import CliRunner

from osh.commands.update_cmd import update
from osh.update import core, store
from osh.update.fingerprint import fingerprint_module, fingerprint_project_modules


@pytest.fixture
def module_dir(tmp_project):
    """Return a factory creating minimal Odoo modules in the project."""

    def _make(name, files=None):
        module = tmp_project / name
        module.mkdir()
        (module / "__manifest__.py").write_text("{}\n")
        for rel, content in (files or {}).items():
            path = module / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        return module

    return _make


@pytest.fixture
def fake_db(monkeypatch):
    """Resolve any database name without touching PostgreSQL."""
    monkeypatch.setattr(
        "osh.commands.update_cmd.resolve_db_name_for_run", lambda base: "testdb"
    )


@pytest.fixture
def capture_update(monkeypatch):
    """Capture ``core.update_and_record`` calls instead of running odoo."""
    calls = []

    def _fake(base, db_name, targets, **kwargs):
        calls.append({"db_name": db_name, "targets": targets, **kwargs})

    monkeypatch.setattr(core, "update_and_record", _fake)
    return calls


# Command behaviour ------------------------------------------------------


def test_update_explicit_modules(in_project, fake_db, capture_update):
    """Explicit module names are updated unconditionally, sorted."""
    result = CliRunner().invoke(update, ["mod_b", "mod_a", "mod_a"])

    assert result.exit_code == 0, result.output
    assert len(capture_update) == 1
    assert capture_update[0]["targets"] == ["mod_a", "mod_b"]
    assert capture_update[0]["db_name"] == "testdb"


def test_update_db_option_sanitized(in_project, capture_update):
    """An explicit -d value is sanitized and used directly."""
    result = CliRunner().invoke(update, ["my_mod", "-d", "My DB!"])

    assert result.exit_code == 0, result.output
    assert capture_update[0]["db_name"] == "my-db"


def test_update_auto_detects_targets(in_project, fake_db, capture_update, monkeypatch):
    """No module arguments runs change detection and updates its result."""
    monkeypatch.setattr(core, "detect_targets", lambda *a, **kw: ["my_mod"])

    result = CliRunner().invoke(update, [])

    assert result.exit_code == 0, result.output
    assert capture_update[0]["targets"] == ["my_mod"]


def test_update_no_changes(in_project, fake_db, capture_update, monkeypatch):
    monkeypatch.setattr(core, "detect_targets", lambda *a, **kw: [])

    result = CliRunner().invoke(update, [])

    assert result.exit_code == 0, result.output
    assert "up to date" in result.output
    assert capture_update == []


def test_update_baseline_or_status_short_circuits(
    in_project, fake_db, capture_update, monkeypatch
):
    """detect_targets returning None (baseline/status) runs no update."""
    monkeypatch.setattr(core, "detect_targets", lambda *a, **kw: None)

    result = CliRunner().invoke(update, ["--status"])

    assert result.exit_code == 0, result.output
    assert capture_update == []


def test_update_status_rejects_modules(in_project, fake_db):
    result = CliRunner().invoke(update, ["--status", "my_mod"])
    assert result.exit_code != 0
    assert "--status" in result.output


def test_update_modules_reject_all(in_project, fake_db):
    result = CliRunner().invoke(update, ["my_mod", "--all"])
    assert result.exit_code != 0
    assert "--all" in result.output


def test_update_forwards_backend_options(in_project, fake_db, capture_update):
    result = CliRunner().invoke(
        update,
        ["my_mod", "--target", "docker", "--compose-file", "x.yml", "--dry-run"],
    )

    assert result.exit_code == 0, result.output
    call = capture_update[0]
    assert call["backend_name"] == "docker"
    assert call["compose_file"] == "x.yml"
    assert call["dry_run"] is True


# Fingerprinting ---------------------------------------------------------


def test_fingerprint_changes_on_code_change(module_dir):
    module = module_dir("my_mod", files={"models.py": "a = 1\n"})
    before = fingerprint_module(module)
    (module / "models.py").write_text("a = 2\n")
    assert fingerprint_module(module) != before


def test_fingerprint_ignores_static(module_dir):
    module = module_dir("my_mod")
    before = fingerprint_module(module)
    static = module / "static" / "src"
    static.mkdir(parents=True)
    (static / "app.js").write_text("console.log(1)\n")
    assert fingerprint_module(module) == before


def test_fingerprint_ignores_pycache(module_dir):
    module = module_dir("my_mod")
    before = fingerprint_module(module)
    cache = module / "__pycache__"
    cache.mkdir()
    (cache / "models.cpython-314.pyc").write_bytes(b"\x00\x01")
    assert fingerprint_module(module) == before


def test_fingerprint_nested_repos(tmp_project, module_dir):
    """Nested repos are included by default, skipped with skip_nested."""
    module_dir("my_mod")
    for repo in ("odoo", "enterprise"):
        source = tmp_project / repo
        source.mkdir()
        (source / ".git").touch()  # submodule/clone marker
        nested = source / f"{repo}_mod"
        nested.mkdir()
        (nested / "__manifest__.py").write_text("{}\n")

    discovered = fingerprint_project_modules(tmp_project)
    assert set(discovered) == {"my_mod", "odoo_mod", "enterprise_mod"}

    filtered = fingerprint_project_modules(tmp_project, skip_nested=True)
    assert filtered == {"my_mod": fingerprint_module(tmp_project / "my_mod")}


def test_fingerprint_upstream_only_filter(tmp_project, module_dir):
    """upstream=False keeps nested third-party repos, drops upstream trees."""
    module_dir("my_mod")
    upstream = tmp_project / "odoo"
    upstream.mkdir()
    (upstream / ".git").touch()
    nested = upstream / "odoo_mod"
    nested.mkdir()
    (nested / "__manifest__.py").write_text("{}\n")

    discovered = fingerprint_project_modules(tmp_project, upstream=False)
    assert set(discovered) == {"my_mod"}


# Store (SQL helpers, psql mocked) ----------------------------------------


def test_get_module_states_parses_rows(monkeypatch, tmp_project):
    monkeypatch.setattr(
        store,
        "_psql",
        lambda *a, **kw: (0, "base\tinstalled\nmy_mod\tto upgrade\n", ""),
    )
    assert store.get_module_states(tmp_project, "db") == {
        "base": "installed",
        "my_mod": "to upgrade",
    }


def test_get_module_states_uninitialized_db(monkeypatch, tmp_project):
    monkeypatch.setattr(
        store,
        "_psql",
        lambda *a, **kw: (1, "", 'relation "ir_module_module" does not exist'),
    )
    assert store.get_module_states(tmp_project, "db") is None


def test_read_fingerprints_parses_json(monkeypatch, tmp_project):
    monkeypatch.setattr(store, "_psql", lambda *a, **kw: (0, '{"my_mod": "abc"}\n', ""))
    assert store.read_fingerprints(tmp_project, "db") == {"my_mod": "abc"}


def test_read_fingerprints_empty_means_no_baseline(monkeypatch, tmp_project):
    monkeypatch.setattr(store, "_psql", lambda *a, **kw: (0, "", ""))
    assert store.read_fingerprints(tmp_project, "db") is None


def test_write_fingerprints_upserts(monkeypatch, tmp_project):
    captured = []

    def _fake(base, db_name, sql, **kw):
        captured.append(sql)
        return 0, "", ""

    monkeypatch.setattr(store, "_psql", _fake)
    store.write_fingerprints(tmp_project, "db", {"my_mod": "abc"})

    assert len(captured) == 1
    assert "ON CONFLICT (key)" in captured[0]
    assert store.FINGERPRINT_PARAM in captured[0]
    assert "my_mod" in captured[0]
