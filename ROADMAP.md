# Osh Roadmap

This document tracks planned improvements and future development work for the Osh project.

## Release blockers for 0.1.0

These items are small, high-impact fixes that should be completed before the first public release.

- Remove duplicated `_has_arg` from `osh/commands/run_cmd.py` and import it from `osh/commons.py`.
- Replace bare `except Exception` clauses with specific exceptions or add logging for broken plugins, bad user config, and failed plugin loads in `osh/plugin_loader.py` and `osh/userconfig.py`.
- Resolve the `osh/commands/odoo_cmd.py` circular-import workaround so `run_cmd.run` can be imported at module level.
- Standardize echo usage: decide whether `osh run` and backends should use the cached top-level echo functions or explicitly create `Echo` instances via `get_echo()`, then remove the hybrid approach.

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

## Already completed

- Refactored echo system to cached top-level functions (`error`, `warning`, `info`, `friendly`, `internal`) with thread-safe access.
- Fixed inconsistent config precedence and added input validation for verbosity/emoji in `osh/echo.py`.
- Moved `confirm()` to a module-level function and updated callers in `osh/sources.py`.
- Centralized project-config reading helper `_read_project_config()` in `osh/userconfig.py`.
- Simplified `osh init` progress display from `[1/4]` to `[1]`.

## Future ideas

- Add machine-readable / JSON output to `osh doctor` for CI/CD integration.
- Add plugin metadata (version, author, description) and display it in `osh plug list`.
- Expand test coverage for configuration handling, edge cases, and full end-to-end workflows.
