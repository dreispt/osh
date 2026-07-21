# Osh Roadmap

This document tracks planned improvements and future development work for the Osh project.

## Configuration System Improvements

### Unified Config Utility Module

**Status:** Planned
**Priority:** Medium

Create a unified configuration utility module to handle read/write operations for both project-level and user/system-level configurations.

**Current Issues:**

- Configuration handling is scattered across multiple modules (`userconfig.py`, `commons.py`, `echo.py`)
- Different formats are used inconsistently (TOML for user config, INI for project config)
- No unified API for configuration operations
- Code duplication in config reading logic

**Proposed Solution:**

- Create a new `osh/config.py` module that provides:
  - Consistent API for both project and user configs
  - Format abstraction (TOML vs INI vs future formats)
  - Centralized read/write operations
  - Better reusability across the codebase
  - Clear separation between user-level (`~/.config/osh/config.toml`) and project-level (`.osh/config`) configurations

**Benefits:**

- Single source of truth for configuration operations
- Easier to add new configuration options
- Better testability of configuration logic
- Cleaner, more maintainable codebase
- Consistent error handling and validation

**Related Files:**

- `osh/userconfig.py` - Currently handles user-level TOML config
- `osh/commons.py` - Has some config path helpers
- `osh/echo.py` - Recently added `_read_project_config()` for project-level INI config

**Implementation Notes:**

- Maintain backward compatibility with existing config files
- Consider migration path if format changes are needed
- Ensure proper error handling for missing or malformed configs
- Add comprehensive tests for the new module

## Plugin API Critique

This section evaluates how friendly the current plugin API is for third-party
extension authors.

### What works well

- **Small surface area**: there are only two concepts to learn, commands and
  backends, and both are simple Python objects.
- **Familiar tools**: commands are standard Click commands; backends are plain
  Python classes inheriting from `Backend`.
- **Built-in examples**: `osh_local`, `osh_docker` and `osh_test` provide
  realistic reference implementations.
- **Automatic command namespacing**: command name collisions are resolved by
  prefixing the plugin source, which keeps `osh` stable when multiple plugins are
  installed.
- **Reusable diagnostics**: the `Diagnostics` dataclass is reused by
  `osh doctor`, `osh init` and `osh run`, so backend authors do not have to
  write separate reporting code.

### Pain points

- **Broad `**options`signatures**:`init`, `diagnose`, `run`and`restore`all
accept`\*\*options`but do not document which keys are actually passed. The
only way to know is to trace`init_cmd.py`, `run_cmd.py`and`restore_cmd.py`.
- **No dependency mechanism**: `osh` does not declare or install plugin
  dependencies. Authors must document external packages and trust users to
  install them.
- **`ctx` usage is inconsistent**: `diagnose` receives `ctx` but most backends
  use it only to read `ctx.params` for CLI overrides. The exact CLI options that
  are forwarded to each method differ between commands.

### Improvements implemented

- `Backend.make_init_option()` now sets `target_group` automatically.
- `Backend.run()` receives a `RunSpec` dataclass with `argv`, `db_name`,
  `config_path`, `extra_args` and `executable` fields.
- `plugin_loader.load_backends()` warns when a backend name collision causes a
  plugin backend to be skipped.
- Plugins can be distributed via the `osh.plugins` Python entry point group in
  addition to `~/.config/osh/plugins/`.
- Version detection is centralized in `osh/version.py`; `Backend.detect_odoo_version`
  has a default implementation that delegates to it.
- `osh/commons.py` provides `run_subprocess()` for `(returncode, stdout, stderr)`
  calls without Click exception formatting.
- `osh/sources.py` uses a `SourceResolver` class to plan source copies per target.
- `osh init --target <name>` is the single init command; `init-local` and
  `init-docker` aliases have been removed. `osh init --help` and `osh run --help`
  display a `Targets` section with available backends.

### Remaining suggestions

- Document the exact keys passed in `**options` for each lifecycle method, or
  replace `**options` with named keyword arguments.
- Consider a plugin manifest (e.g. `pyproject.toml` `[tool.osh.plugins]`) so
  metadata such as dependencies and target names can be declared statically.
