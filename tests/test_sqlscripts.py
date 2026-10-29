"""Tests for the layered SQL script resolver in ``osh.sqlscripts``."""

import pytest

from osh.sqlscripts import filter_sql_scripts, resolve_sql_scripts, run_sql_scripts


@pytest.fixture
def user_dir(tmp_path, monkeypatch):
    """Redirect ``~/.config/osh/<kind>`` to a temporary directory."""
    directory = tmp_path / "user-scripts"
    monkeypatch.setattr(
        "osh.sqlscripts.get_user_scripts_dir", lambda kind: directory / kind
    )
    return directory


def _names(scripts):
    return [script.name for script in scripts]


def _by_name(scripts, name):
    return next(script for script in scripts if script.name == name)


def test_bundled_neutralize_scripts_resolve_without_init(tmp_project, user_dir):
    """The bundled defaults resolve directly — nothing is copied at init."""
    scripts = resolve_sql_scripts(tmp_project, "neutralize")

    assert _names(scripts) == ["000_osh_default.sql", "900_clear_assets.sql"]
    assert all(script.layer == "bundled" for script in scripts)
    assert (
        "DELETE FROM ir_attachment"
        in _by_name(scripts, "900_clear_assets.sql").read_text()
    )


def test_bundled_predicate_filters_the_bundled_layer(tmp_project, user_dir):
    """Conditional bundled defaults — e.g. the pre-16 fallback — can be dropped."""
    scripts = resolve_sql_scripts(
        tmp_project, "neutralize", bundled=lambda name: name != "000_osh_default.sql"
    )

    assert _names(scripts) == ["900_clear_assets.sql"]


def test_user_layer_adds_and_overrides(tmp_project, user_dir):
    """User scripts merge in and same-named ones shadow bundled defaults."""
    (user_dir / "neutralize").mkdir(parents=True)
    (user_dir / "neutralize" / "900_clear_assets.sql").write_text("-- mine\n")
    (user_dir / "neutralize" / "100_extra.sql").write_text("-- extra\n")

    scripts = resolve_sql_scripts(tmp_project, "neutralize")

    assert _names(scripts) == [
        "000_osh_default.sql",
        "100_extra.sql",
        "900_clear_assets.sql",
    ]
    overridden = _by_name(scripts, "900_clear_assets.sql")
    assert overridden.layer == "user"
    assert overridden.read_text() == "-- mine\n"


def test_project_layer_overrides_user_and_bundled(tmp_project, user_dir):
    """A same-named project script is the last word."""
    (user_dir / "neutralize").mkdir(parents=True)
    (user_dir / "neutralize" / "050_task.sql").write_text("-- user\n")
    project_dir = tmp_project / ".osh" / "neutralize"
    project_dir.mkdir(parents=True)
    (project_dir / "050_task.sql").write_text("-- project\n")

    scripts = resolve_sql_scripts(tmp_project, "neutralize")
    script = _by_name(scripts, "050_task.sql")

    assert script.layer == "project"
    assert script.read_text() == "-- project\n"


def test_filter_only_and_skip(tmp_project, user_dir):
    """``--script`` keeps named scripts, ``--skip`` drops them."""
    (tmp_project / ".osh" / "neutralize").mkdir(parents=True)
    for name in ("010_a.sql", "020_b.sql", "030_c.sql"):
        (tmp_project / ".osh" / "neutralize" / name).write_text(f"-- {name}\n")
    scripts = resolve_sql_scripts(tmp_project, "neutralize", bundled=lambda name: False)

    only = filter_sql_scripts(scripts, only=("020_b.sql",))
    assert _names(only) == ["020_b.sql"]

    skipped = filter_sql_scripts(scripts, skip=("010_a.sql", "030_c.sql"))
    assert _names(skipped) == ["020_b.sql"]


def test_filter_unknown_name_is_a_usage_error(tmp_project, user_dir):
    """Naming a script outside the resolved set fails with the set listed."""
    scripts = resolve_sql_scripts(tmp_project, "neutralize")

    with pytest.raises(Exception, match="Unknown script.*nope.sql"):
        filter_sql_scripts(scripts, only=("nope.sql",))


def test_run_sql_scripts_dry_run_runs_nothing(tmp_project, user_dir, capsys):
    """``--dry-run`` names each script and its layer without touching psql."""
    scripts = resolve_sql_scripts(tmp_project, "neutralize")

    run_sql_scripts(tmp_project, "neutralize", "somedb", scripts, dry_run=True)

    out = capsys.readouterr().err
    assert "Would run neutralize script: 000_osh_default.sql (bundled)" in out
    assert "Would run neutralize script: 900_clear_assets.sql (bundled)" in out
