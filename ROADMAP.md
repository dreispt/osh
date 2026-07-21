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
