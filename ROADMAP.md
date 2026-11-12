# Osh Roadmap

This document tracks planned improvements and future development work for the Osh project.

## Agent-friendly surface

AI agents increasingly drive `osh` inside Odoo projects — installing
environments, copying databases, running tests, debugging failures. The
CLI is usable today (every command has `--help`, `--dry-run`,
`--verbose`/`--debug`, and a bundled agent skill describes the workflow),
but a few gaps make automation fragile:

- **Machine-readable output.** `db list`, `db show`, `config show`,
  `backup list`, `stop --list` all print human-oriented tables. A
  `--json` flag (or `--format json`) would let agents consume results
  without fragile text parsing.
- **A resolved-environment command.** Agents currently assemble by hand
  the facts they need to compose commands: project root, env root,
  active runtime, branch→database name, odoo config path, addons path,
  Python. An `osh env`/`osh info` command printing those — ideally both
  human-readable and `--json` — would be the single source of truth.
- **Guaranteed non-interactive mode.** Prompts depend on TTY detection;
  a global `--no-input` flag (or `OSH_NO_INPUT` env var) that fails fast
  with a clear "pass `--flag X`" error — plus extending `--yes` beyond
  `osh init` — would stop agents from silently blocking on menus.
- **Branch-aware database copies.** `osh db copy` makes the caller
  compute the `<project>-<branch>` target name, which is exactly the
  kind of derivation agents get wrong. A `--current`/`--to-branch`
  target — or defaulting the target to the current branch's database
  when omitted — removes that failure mode.
- **Documented exit-code contract.** Exit codes are currently
  incidental. Documenting which codes mean "not inside an Osh project",
  "input required", "command failed" would let agents branch reliably
  instead of inspecting stderr text.

The revived `osh doctor` (see below) doubles as the agent health check —
worth giving it `--json` output from the start.

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
