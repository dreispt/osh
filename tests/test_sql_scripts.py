"""Tests for the layered SQL script resolver in ``osh.sql_scripts``."""

import pytest

from osh.plugins.osh_backup.restore_ops import (
    FALLBACK_SCRIPT,
    neutralize_with_sql,
    resolve_neutralize_scripts,
)
from osh.sql_scripts import (
    filter_sql_scripts,
    prune_redundant_project_scripts,
    resolve_sql_scripts,
    run_sql_scripts,
)


@pytest.fixture
def user_dir(tmp_path, monkeypatch):
    """Redirect ``~/.config/osh/<kind>`` to a temporary directory."""
    directory = tmp_path / "user-scripts"
    monkeypatch.setattr(
        "osh.sql_scripts.get_user_scripts_dir", lambda kind: directory / kind
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


def test_tail_match_does_not_pick_shorter_tails(tmp_project, user_dir):
    """``mail_message`` picks ``020_mail_message.sql``, not ``010_mail.sql``."""
    (tmp_project / ".osh" / "neutralize").mkdir(parents=True)
    for name in ("010_mail.sql", "020_mail_message.sql"):
        (tmp_project / ".osh" / "neutralize" / name).write_text(f"-- {name}\n")
    scripts = resolve_sql_scripts(tmp_project, "neutralize", bundled=lambda name: False)

    assert _names(filter_sql_scripts(scripts, only=("mail_message",))) == [
        "020_mail_message.sql"
    ]
    assert _names(filter_sql_scripts(scripts, only=("mail",))) == [
        "010_mail.sql",
        "020_mail_message.sql",
    ]


def test_dry_run_does_not_print_script_bodies(tmp_project, user_dir, capsys):
    """Script bodies may hold credentials — dry runs only name the scripts."""
    (tmp_project / ".osh" / "neutralize").mkdir(parents=True)
    (tmp_project / ".osh" / "neutralize" / "100_secret.sql").write_text(
        "-- s3cret-token\n"
    )
    scripts = resolve_sql_scripts(tmp_project, "neutralize")

    run_sql_scripts(tmp_project, "neutralize", "somedb", scripts, dry_run=True)

    out = capsys.readouterr().err
    assert "100_secret.sql (project)" in out
    assert "s3cret-token" not in out


def test_prune_removes_untouched_legacy_copies(tmp_project, user_dir):
    """Copies identical to the bundled or user script go; edited ones stay."""
    bundled = resolve_sql_scripts(tmp_project, "neutralize")
    (user_dir / "neutralize").mkdir(parents=True)
    (user_dir / "neutralize" / "100_custom.sql").write_text("-- central\n")
    project_dir = tmp_project / ".osh" / "neutralize"
    project_dir.mkdir(parents=True)
    for script in bundled:
        (project_dir / script.name).write_text(script.read_text())
    (project_dir / "100_custom.sql").write_text("-- central\n")
    (project_dir / "200_edited.sql").write_text("-- project\n")

    prune_redundant_project_scripts(tmp_project, "neutralize")

    assert sorted(path.name for path in project_dir.iterdir()) == ["200_edited.sql"]


def test_restore_neutralize_scripts_drop_fallback_from_every_layer(
    tmp_project, user_dir
):
    """A copied or overriding ``000_osh_default.sql`` is never a post-hook."""
    project_dir = tmp_project / ".osh" / "neutralize"
    project_dir.mkdir(parents=True)
    (project_dir / FALLBACK_SCRIPT).write_text("-- custom fallback\n")

    assert _names(resolve_neutralize_scripts(tmp_project)) == ["900_clear_assets.sql"]


def test_sql_fallback_honours_overrides(tmp_project, user_dir, monkeypatch):
    """The SQL fallback runs the project override instead of the bundled one."""
    project_dir = tmp_project / ".osh" / "neutralize"
    project_dir.mkdir(parents=True)
    (project_dir / FALLBACK_SCRIPT).write_text("-- custom fallback\n")
    ran = []
    monkeypatch.setattr(
        "osh.plugins.osh_backup.restore_ops.run_psql_script",
        lambda base, db, script, **kw: ran.append(script.read_text()),
    )

    neutralize_with_sql(tmp_project, "somedb")

    assert ran == ["-- custom fallback\n"]
