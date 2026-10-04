# Osh Roadmap

This document tracks planned improvements and future development work for the Osh project.

## Plugin API improvements

- Document the exact keys passed in `**options` for each lifecycle method, or replace `**options` with named keyword arguments.
- Extend `osh-plugin.toml` with optional metadata (e.g. a minimum `osh` version) and surface it in `osh plug list`; `description` and `depends` (plugin load ordering) are already handled.
- Graduate the built-in `osh_backup` plugin (`osh backup get`, `osh backup restore`, bundled source schemes) into a separately distributed repository once the plugin boundary has proven stable.

## Deprecated compatibility aliases

To be removed in a later release, once the plugin ecosystem has had time to migrate:

- `osh.backends` (`Backend`/`HostBackend`/`NoneBackend`), `osh.commands.backend_cmd` (`BackendCommands`), the `osh.db` `*_backend` helpers, `plugin_loader`'s `get_backend_class`/`load_backends`/`backend_meta`, and the `[backends]` table.
- The hidden `osh backend` CLI alias and the `none`/`local` runtime name aliases.
- The `init.target` record written by `osh <runtime> init`.

## `osh runtime` as the entry point to the active runtime

`osh runtime <verb>` should delegate to the active runtime — `osh runtime status` doing what `osh docker status` does when docker is active, the same way `osh runtime stop` already delegates to `resolve_runtime(base).stop()`. `list`/`activate`/`deactivate` stay selection verbs (`osh runtime list` would collide with runtime-provided `list` commands like `osh docker list`).

- `Runtime.status(ctx, base)` method (parallel to `stop`), a `status` subcommand on `RuntimeCommands`, and `RuntimeCtl.status` delegating to `resolve_runtime(base).status()`.
- `osh runtime status` keeps answering "which is active" — prefix the delegated output with the runtime's name.

## `host` command group

`host` is the only runtime without an `osh <runtime>` group — a core `HostCommands(RuntimeCommands)` gives it `init`/`activate`/`status`/`stop` for symmetry (`osh host init` ≈ `osh init`). `HostRuntime` itself stays in core: it is the unconditional default `resolve_runtime` must return and the `VenvRuntime` base class, so a plugin package would add coupling rather than remove it.

## Plugin test discovery

- Move each plugin's tests into its own plugin directory (e.g. `osh/plugins/osh_runtime_docker/tests/`) instead of the top-level `tests/` directory, and add a mechanism to discover and run them — similar to what is already implemented in `dreispt/osh-contrib`.

## `osh doctor` (removed — to be redesigned)

The `osh doctor` and `osh <runtime> doctor` commands were removed; the diagnostics machinery behind them stays and is still used by `osh init`/`osh odoo`/`osh shell` pre-flight (`Runtime.diagnose`, `Diagnostics`, `collect_diagnostics`, `check_run_diagnostics` in `osh/commands/helpers.py`). Design notes for bringing it back:

- Reintroduce `osh doctor` as a `CommandHandler` (`_cli_name = "doctor"`) so plugins extend it by subclassing — e.g. a plugin adds its own check sections via `extends = ["doctor"]`.
- Per-runtime detail can come back as `osh <runtime> doctor` or fold into the core command by delegating to the active runtime's `diagnose(phase="doctor")` — the `"doctor"` phase convention and `diagnose_sections_for_phase` are still in place.
- The removed helpers to restore: `Diagnostics.report()` and `report_diagnostics()` (topic-sectioned output), and the doctor-phase project-layout diagnostics (`_add_nesting_diagnostics`) which reported enclosing/nested `.osh` environments — init still guards nesting via `_check_nesting` in `init_cmd.py`, and the `init.parent` record it writes is still there to acknowledge intentional nesting.
- `Diagnostics` keeps collecting `info` entries during `init`/`run` phases (e.g. `osh odoo` reads `info["<runtime>"]["odoo_executable"]`), so a revived doctor can render what runtimes already collect.
