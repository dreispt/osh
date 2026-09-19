"""Disabled plugin — writes a marker file if it is ever imported."""

import pathlib

pathlib.Path(__file__).resolve().with_name("imported").write_text("ran")
