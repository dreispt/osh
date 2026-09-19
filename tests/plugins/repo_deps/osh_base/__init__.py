"""Dependency plugin — writes a marker file when imported."""

import pathlib

pathlib.Path(__file__).resolve().with_name("base_loaded").write_text("base")
