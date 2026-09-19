"""Dependent plugin — fails to import unless osh-base loaded first."""

import pathlib

_marker = pathlib.Path(__file__).resolve().parent.parent / "osh_base" / "base_loaded"
assert _marker.exists(), "base not loaded"
