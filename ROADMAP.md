# Osh Roadmap

This document tracks planned improvements and future development work for the Osh project.

## Plugin API improvements

- Document the exact keys passed in `**options` for each lifecycle method, or
  replace `**options` with named keyword arguments.
- Consider a plugin manifest (e.g. `pyproject.toml` `[tool.osh.plugins]`) so
  metadata such as dependencies and target names can be declared statically.
