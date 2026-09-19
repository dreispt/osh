# Osh Roadmap

This document tracks planned improvements and future development work for the Osh project.

## Plugin API improvements

- Document the exact keys passed in `**options` for each lifecycle method, or replace `**options` with named keyword arguments.
- Extend `osh-plugin.toml` with optional metadata (e.g. dependencies, minimum `osh` version) and surface it in `osh plug list`; `description` is already shown.
- Graduate the built-in `osh_db_get` plugin (`osh db get`, `osh db restore`, bundled source schemes) into a separately distributed repository once the plugin boundary has proven stable.
- Extend the command-handler model (`osh.handlers.CommandHandler`, extension by subclassing — see `PLUGINS.md`) to the remaining commands (`shell`, `db shell`, `db show`/`set`/`copy`/`unset`, `init`, `switch`, `config`, `plug`, `backend`), so the whole command surface is extensible the same way.

## `osh doctor` (removed — to be redesigned)

The `osh doctor` and `osh <backend> doctor` commands were removed; the diagnostics machinery behind them stays and is still used by `osh init`/`osh odoo`/`osh shell` pre-flight (`Backend.diagnose`, `Diagnostics`, `collect_diagnostics`, `check_run_diagnostics` in `osh/commands/helpers.py`). Design notes for bringing it back:

- Reintroduce `osh doctor` as a `CommandHandler` (`_cli_name = "doctor"`) so plugins extend it by subclassing — e.g. a plugin adds its own check sections via `extends = ["doctor"]`.
- Per-backend detail can come back as `osh <backend> doctor` or fold into the core command by delegating to the active backend's `diagnose(phase="doctor")` — the `"doctor"` phase convention and `diagnose_sections_for_phase` are still in place.
- The removed helpers to restore: `Diagnostics.report()` and `report_diagnostics()` (topic-sectioned output), and the project-layout checks (`check_nesting` on `collect_diagnostics`, `_add_nesting_diagnostics`) which reported enclosing/nested `.osh` environments — the `init.parent` record written by `osh init` is still there to acknowledge intentional nesting.
- `Diagnostics` keeps collecting `info` entries during `init`/`run` phases (e.g. `osh odoo` reads `info["<backend>"]["odoo_executable"]`), so a revived doctor can render what backends already collect.

## `osh switch`

Current state: a git-less project root switches every repository found below it (`find_project_repos`). A git-rooted project switches only the root repository — git-managed content below it is left alone, and a `.gitmodules` hint is printed instead of acting.

- Git-rooted projects: also handle git-managed subdirectories.
  - Nested source clones (e.g. `odoo`/`enterprise`/`design-themes`): switch to the same target branch when the clone has it.
  - Submodules are pinned to a commit by the parent repo — after switching the root, run `git submodule update --init` to check out the referenced commits and materialize newly added submodules.
