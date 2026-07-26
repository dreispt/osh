# Osh Roadmap

This document tracks planned improvements and future development work for the Osh project.

## Plugin API improvements

- Document the exact keys passed in `**options` for each lifecycle method, or replace `**options` with named keyword arguments.
- Consider a plugin manifest (e.g. `pyproject.toml` `[tool.osh.plugins]`) so metadata such as dependencies and target names can be declared statically.

## Code-quality and simplification opportunities

- Extend `osh/common.py` with a standard subprocess helper for capture/error handling and replace ad-hoc `subprocess` calls in `osh/sources.py`, `osh/commands/backup_sources.py`, and plugins.
- Extract `osh/sources.py` source-resolution logic into a `SourceResolver` class to reduce nested conditionals and parameter passing.
