# Osh Roadmap

This document tracks planned improvements and future development work for the Osh project.

## Plugin API improvements

- Document the exact keys passed in `**options` for each lifecycle method, or replace `**options` with named keyword arguments.
- Graduate the built-in `osh_backup` plugin (`osh backup get`, `osh backup restore`, bundled source schemes) into a separately distributed repository once the plugin boundary has proven stable.

## Deprecated compatibility aliases

To be removed in a later release, once the plugin ecosystem has had time to migrate:

- `osh.backends` (`Backend`/`HostBackend`/`NoneBackend`), the `osh.db` `*_backend` helpers, `plugin_loader`'s `get_backend_class`/`load_backends`/`backend_meta`, and the `[backends]` table.
- The `none`/`local` runtime name aliases.
- The `init.target` record written by `osh init --runtime=<name>`.
- The `osh-plugin.toml` marker — deprecated: `[tool.osh]` in `pyproject.toml` is the canonical declaration. Removal needs a replacement channel for the built-in plugins under `osh/plugins/`, which have no `pyproject.toml` of their own today.

## Plugin test discovery

- Move each plugin's tests into its own plugin directory (e.g. `osh/plugins/osh_runtime_docker/tests/`) instead of the top-level `tests/` directory, and add a mechanism to discover and run them — similar to what is already implemented in `dreispt/osh-contrib`.

## Runtime-specific `osh init` options

`osh init --help` currently merges every runtime's init options into one flag list (`--service`, `--compose-file`, `--odoo-source`, `--odoo-command`, ...), so options for runtimes you never use pollute the help — and foreign options only produce a runtime warning. Move each runtime's options behind its own subcommand (e.g. `osh init docker --service odoo`, keeping `--runtime=<name>` for compatibility), so `osh init --help` shows only core options and each runtime exposes its flags where they apply. With runtimes as subcommands, the separate "Runtimes" help section can fold into the normal "Commands" section — each runtime is just a command in the list. Foreign-option warnings may become unnecessary once options live under their runtime.

## Switch ports on Docker

- Consider what `osh odoo -p <port>` should do in Docker environments regarding the build-image state: the port override already forces a stack recreate, but the stale-input check and fingerprint handling apply independently of it.

## `osh doctor` (removed — to be redesigned)

The `osh doctor` command was removed; the diagnostics machinery behind it stays and is still used by `osh init`/`osh odoo`/`osh shell` pre-flight (`Runtime.diagnose`, `Diagnostics`, `collect_diagnostics`, `check_run_diagnostics` in `osh/commands/helpers.py`). Design notes for bringing it back:

- Reintroduce `osh doctor` as a `CommandHandler` (`_cli_name = "doctor"`) so plugins extend it by subclassing — e.g. a plugin adds its own check sections via `extends = ["doctor"]`.
- Per-runtime detail can fold into the core command by delegating to the active runtime's `diagnose(phase="doctor")` — the `"doctor"` phase convention and `diagnose_sections_for_phase` are still in place.
- The removed helpers to restore: `Diagnostics.report()` and `report_diagnostics()` (topic-sectioned output), and the doctor-phase project-layout diagnostics (`_add_nesting_diagnostics`) which reported enclosing/nested `.osh` environments — init still guards nesting via `_check_nesting` in `init_cmd.py`, and the `init.parent` record it writes is still there to acknowledge intentional nesting.
- `Diagnostics` keeps collecting `info` entries during `init`/`run` phases (e.g. `osh odoo` reads `info["<runtime>"]["odoo_executable"]`), so a revived doctor can render what runtimes already collect.
