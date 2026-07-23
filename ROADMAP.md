# Osh Roadmap

This document tracks planned improvements and future development work for the Osh project.

## Configuration system improvements

- Create a unified `osh/config.py` module to handle read/write operations for both project-level (`~/.osh/config`) and user/system-level (`~/.config/osh/config.toml`) configurations.
  - Abstract format differences (INI for project config, TOML for user config).
  - Centralize read/write operations and validation.
  - Eliminate duplicated config-reading logic across `osh/userconfig.py`, `osh/db.py`, and `osh/commands/config_cmd.py`.

## Plugin API improvements

- Document the exact keys passed in `**options` for each lifecycle method, or replace `**options` with named keyword arguments.
- Consider a plugin manifest (e.g. `pyproject.toml` `[tool.osh.plugins]`) so metadata such as dependencies and target names can be declared statically.

## Code-quality and simplification opportunities

- Consolidate version detection into `osh/version.py` to reduce duplication across `osh/sources.py`, `osh/db.py`, and backend implementations.
- Extend `osh/commons.py` with a standard subprocess helper for capture/error handling and replace ad-hoc `subprocess` calls in `osh/sources.py`, `osh/backup_sources.py`, and plugins.
- Refactor `osh/plugins/osh_docker/backends.py` `diagnose()` into section-specific methods (currently ~140 lines).
- Extract `osh/sources.py` source-resolution logic into a `SourceResolver` class to reduce nested conditionals and parameter passing.
- Remove or consolidate the `init-local` and `init-docker` alias commands if they duplicate `osh init --target <name>`.
