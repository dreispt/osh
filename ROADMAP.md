# Osh Roadmap

This document tracks planned improvements and future development work for the Osh project.

## Plugin API improvements

- Document the exact keys passed in `**options` for each lifecycle method, or replace `**options` with named keyword arguments.
- Extend the `OSH_PLUGIN_MANIFEST` dict with optional metadata (e.g. description, dependencies, minimum `osh` version) and surface it in `osh plug list`.
- Graduate the built-in `osh_db_get` plugin (`osh db get`, `osh db restore`, bundled source schemes) into a separately distributed repository once the plugin boundary has proven stable.
- Extend the operation-class model (`osh.operations`, the `@extends` decorator — see `PLUGINS.md`) to the remaining commands (`shell`, `db shell`, `db show`/`set`/`copy`/`unset`, `init`, `switch`, `doctor`, `config`, `plug`, `backend`), so the whole command surface is extensible the same way.

## `osh switch`

- For git-rooted projects, also handle git-managed subdirectories (submodules and nested source clones such as `odoo`/`enterprise`/`design-themes`): switch the branch in all of them, not just the project root, and run `git submodule update --init` afterwards to materialize newly referenced submodules. Git-less project roots already switch every repository found below them.
