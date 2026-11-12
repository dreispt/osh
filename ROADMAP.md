# Osh Roadmap

This document tracks planned improvements and future development work for the Osh project.

## Agent-friendly surface

AI agents increasingly drive `osh` inside Odoo projects — installing
environments, copying databases, running tests, debugging failures. The
core surface is now agent-ready: `--json` on the read-only commands,
`osh init info` for the resolved environment, `--no-input`/`OSH_NO_INPUT`
with the exit-code-3 contract (see `docs/SCRIPTING.md`),
`db copy --from-current`/`--to-current`, and the bundled `osh skill`.
Remaining gaps:

- **`--yes` beyond `osh init`.** A global `--yes` stays rejected — it
  cannot answer menus — but scoped `--yes`/`--force` on destructive
  commands (`db drop`, `backup restore`, `db unset`) that currently
  only offer prompts would help non-interactive callers.
- **`stop --list --all --json`.** The structured report is per-project
  today; aggregating it across projects is doable if agents ever manage
  more than one.
- **`osh doctor` with `--json`.** The revived doctor (see below) doubles
  as the agent health check — worth giving it `--json` output from the
  start.

## Plugin API improvements

- Document the exact keys passed in `**options` for each lifecycle method, or replace `**options` with named keyword arguments.
- Graduate the built-in `osh_backup` plugin (`osh backup get`, `osh backup restore`, bundled source schemes) into a separately distributed repository once the plugin boundary has proven stable.

## Plugin test discovery

Built-in plugin tests already live in their plugin directories
(`osh/plugins/<plugin>/tests/`) and run under the same `pytest`
invocation as the top-level suite. What remains is discovering and
running tests shipped by third-party entry-point plugins — similar to
what is already implemented in `dreispt/osh-contrib`.

## Multiple Odoo versions per environment

Let one project carry several Odoo versions side by side and switch between them: `osh init -v 19.0` initialises a new version's environment, and `osh init -v 18.0` initialises or switches to another — each version keeps its own environment under a versioned `.osh/` subdir (`.osh/19.0`, `.osh/18.0`), with the venv moving inside it (`.osh/19.0/.venv`) instead of the project root. The recorded project version becomes per-branch state: a git branch maps to a database _and_ to an Odoo version, so `osh odoo` and `osh exec` resolve the binaries for the branch's target version. Existing projects need detection of the old flat `.osh/` layout versus the new versioned one, plus a migration path offered to the user (e.g. move `.venv` and friends under `.osh/<current-version>/` on the next `osh init`). Other open points: what else is per-version (`odoo.conf`, `docker.toml`, the active runtime, neutralize scripts) versus project-wide.

## uv fallback for unpackagable sdists

`uv pip install` rejects a few very old source distributions (e.g.
`pypdf2==1.26.0`, tarballs without `ustar` headers) that `pip` handles
fine. Today the docs suggest `--installer pip`; consider automatically
retrying the requirements install with pip when uv fails on an sdist.

## Switch ports on Docker

- Consider what `osh odoo -p <port>` should do in Docker environments regarding the build-image state: the port override already forces a stack recreate, but the stale-input check and fingerprint handling apply independently of it.

## `osh doctor` (removed — to be redesigned)

The `osh doctor` command was removed; the diagnostics machinery behind it stays and is still used by `osh init`/`osh odoo`/`osh exec` pre-flight (`Runtime.diagnose`, `Diagnostics`, `collect_diagnostics`, `check_run_diagnostics` in `osh/commands/helpers.py`). Design notes for bringing it back:

- Reintroduce `osh doctor` as a `CommandHandler` (`_cli_name = "doctor"`) so plugins extend it by subclassing — e.g. a plugin adds its own check sections via `extends = ["doctor"]`.
- Per-runtime detail can fold into the core command by delegating to the active runtime's `diagnose(phase="doctor")` — the `"doctor"` phase convention and `diagnose_sections_for_phase` are still in place.
- The removed helpers to restore: `Diagnostics.report()` and `report_diagnostics()` (topic-sectioned output), and the doctor-phase project-layout diagnostics (`_add_nesting_diagnostics`) which reported enclosing/nested `.osh` environments — init still guards nesting via `_check_nesting` in `init_cmd.py`, and the `init.parent` record it writes is still there to acknowledge intentional nesting.
- `Diagnostics` keeps collecting `info` entries during `init`/`run` phases (e.g. `osh odoo` reads `info["<runtime>"]["odoo_executable"]`), so a revived doctor can render what runtimes already collect.
