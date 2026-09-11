# Osh Roadmap

This document tracks planned improvements and future development work for the Osh project.

## Plugin API improvements

- Document the exact keys passed in `**options` for each lifecycle method, or replace `**options` with named keyword arguments.
- Extend the `OSH_PLUGIN_MANIFEST` dict with optional metadata (e.g. description, dependencies, minimum `osh` version) and surface it in `osh plug list`.
