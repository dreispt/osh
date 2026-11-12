"""Tests for the TOML-backed configuration helpers."""

import json
from pathlib import Path

from click.testing import CliRunner

from osh.commands.config_cmd import config
from osh.config import (
    _format_toml_key,
    _format_toml_value,
    get_project_config,
    load_project_config,
    set_project_config,
)


def test_dotted_branch_key_roundtrip(tmp_project):
    """Branch names containing dots are written as quoted TOML keys."""
    branch = "17.0.3.1.1"
    set_project_config(tmp_project, "db", values={branch: "mydb", "last": "mydb"})

    assert get_project_config(tmp_project, "db", branch) == "mydb"
    assert get_project_config(tmp_project, "db", "last") == "mydb"

    config_text = (tmp_project / ".osh" / "config.toml").read_text()
    assert '"17.0.3.1.1" = ' in config_text


def test_load_project_config_repairs_unquoted_dotted_keys(tmp_project):
    """Existing configs with unquoted dotted keys are flattened on load."""
    config_path = tmp_project / ".osh" / "config.toml"
    config_path.write_text("[db]\n17.0.3.1.1 = 'olddb'\n")

    cfg = load_project_config(tmp_project)
    assert cfg.get("db", "17.0.3.1.1") == "olddb"
    assert cfg.get("db", "17") is None


def test_format_toml_key_quotes_non_bare_keys():
    """Keys with dots are quoted; bare-safe keys remain bare."""
    assert _format_toml_key("17.0.3.1.1") == '"17.0.3.1.1"'
    assert _format_toml_key("plain_key-1") == "plain_key-1"


def test_format_toml_value_accepts_path():
    """Path values are serialised as strings."""
    assert _format_toml_value(Path("/tmp/foo")) == "'/tmp/foo'"


def test_config_show_json(tmp_project, monkeypatch):
    """`osh config show --json` emits the stored sections as JSON."""
    set_project_config(tmp_project, "db", "default", "mydb")
    monkeypatch.chdir(tmp_project)

    result = CliRunner().invoke(config, ["show", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["config_file"].endswith(".osh/config.toml")
    assert payload["sections"]["db"]["default"] == "mydb"
