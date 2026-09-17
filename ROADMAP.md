# Osh Roadmap

This document tracks planned improvements and future development work for the Osh project.

## Plugin API improvements

- Document the exact keys passed in `**options` for each lifecycle method, or replace `**options` with named keyword arguments.
- Extend the `OSH_PLUGIN_MANIFEST` dict with optional metadata (e.g. description, dependencies, minimum `osh` version) and surface it in `osh plug list`.
- Graduate the built-in `osh_db_get` plugin (`osh db get`, `osh db restore`, bundled source schemes) into a separately distributed repository once the plugin boundary has proven stable.
- Reconsider hook points for extending core command behaviour (e.g. `db.list_sections`): they don't scale as an extension model. If command operations were implemented as classes behind thin Click wrappers, plugins could extend them with in-place inheritance (Odoo `_inherit`-style) instead of per-feature hook points.

## `osh switch`

- For git-rooted projects, also handle git-managed subdirectories (submodules and nested source clones such as `odoo`/`enterprise`/`design-themes`): switch the branch in all of them, not just the project root, and run `git submodule update --init` afterwards to materialize newly referenced submodules. Git-less project roots already switch every repository found below them.
