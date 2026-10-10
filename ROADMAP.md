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

## `osh apps` command

Add an `osh apps` group for the Odoo module lifecycle — `osh apps list`, `osh apps update`, `osh apps install`, `osh apps uninstall`. Module operations are app-management, not database-management, so they belong under their own group rather than `osh db`: this moves `osh db update` and `osh db installed` (`osh-update`) and `osh db uninstall` (`osh-uninstall`) out of the `db` group — `apps list` covering `db installed` — and adds the missing `install` counterpart. The whole group should probably be implemented in `dreispt/osh-contrib`, not core: a plugin can create the `apps` group itself via `[group_commands.apps]` declarations. Decide whether the `osh db …` spellings stay as deprecated aliases or are dropped at the move.

## Multiple Odoo versions per environment

Let one project carry several Odoo versions side by side and switch between them: `osh init -v 19.0` initialises a new version's environment, and `osh init -v 18.0` initialises or switches to another — each version keeps its own environment under a versioned `.osh/` subdir (`.osh/19.0`, `.osh/18.0`), with the venv moving inside it (`.osh/19.0/.venv`) instead of the project root. The recorded project version becomes per-branch state: a git branch maps to a database _and_ to an Odoo version, so `osh odoo` and `osh exec` resolve the binaries for the branch's target version. Existing projects need detection of the old flat `.osh/` layout versus the new versioned one, plus a migration path offered to the user (e.g. move `.venv` and friends under `.osh/<current-version>/` on the next `osh init`). Other open points: what else is per-version (`odoo.conf`, `docker.toml`, the active runtime, neutralize scripts) versus project-wide.

## Switch ports on Docker

- Consider what `osh odoo -p <port>` should do in Docker environments regarding the build-image state: the port override already forces a stack recreate, but the stale-input check and fingerprint handling apply independently of it.

## `osh doctor` (removed — to be redesigned)

The `osh doctor` command was removed; the diagnostics machinery behind it stays and is still used by `osh init`/`osh odoo`/`osh exec` pre-flight (`Runtime.diagnose`, `Diagnostics`, `collect_diagnostics`, `check_run_diagnostics` in `osh/commands/helpers.py`). Design notes for bringing it back:

- Reintroduce `osh doctor` as a `CommandHandler` (`_cli_name = "doctor"`) so plugins extend it by subclassing — e.g. a plugin adds its own check sections via `extends = ["doctor"]`.
- Per-runtime detail can fold into the core command by delegating to the active runtime's `diagnose(phase="doctor")` — the `"doctor"` phase convention and `diagnose_sections_for_phase` are still in place.
- The removed helpers to restore: `Diagnostics.report()` and `report_diagnostics()` (topic-sectioned output), and the doctor-phase project-layout diagnostics (`_add_nesting_diagnostics`) which reported enclosing/nested `.osh` environments — init still guards nesting via `_check_nesting` in `init_cmd.py`, and the `init.parent` record it writes is still there to acknowledge intentional nesting.
- `Diagnostics` keeps collecting `info` entries during `init`/`run` phases (e.g. `osh odoo` reads `info["<runtime>"]["odoo_executable"]`), so a revived doctor can render what runtimes already collect.
