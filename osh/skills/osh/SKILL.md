---
name: osh
description: Use the osh CLI to set up, run, test, and manage Odoo dev environments — prefer osh commands over hand-rolled venv/docker/psql invocations in any Odoo project.
argument-hint: "[task or question about the environment]"
allowed-tools:
  - exec
  - read
  - grep
  - glob
---

# Working with osh in Odoo projects

`osh` manages Odoo dev environments: runtimes (venv, docker, doodba, host),
per-branch databases, tests, and backups. In an osh-managed project (a
directory containing `.osh/`), **prefer `osh` commands over hand-rolling
equivalents** — they know the project's runtime, database naming, and paths.
Run `osh` from anywhere inside the project — it finds the project root by
walking up, including from subdirectories, git submodules, and linked
worktrees.

## Discover the surface, don't memorize it

The command tree changes as plugins are installed, so always check before
assuming a command exists or guessing its flags:

- `osh --help` — command groups and plugin commands
- `osh <command> --help` — options for a command
- `osh <command> <subcommand> --help` — options for a subcommand
- `osh skill` — prints the current version of this file; re-run it if
  the instructions here look stale

Commands that change something usually accept `--dry-run` to print the
assembled action without performing it; `--verbose` shows the commands
being run and `--debug` adds exit codes and timing. Use these to understand
and diagnose instead of guessing.

## Rules for agents

1. **Non-interactive first.** Many commands prompt (init plan,
   missing-database assistant, database picker). Prefer explicit flags and
   subcommands; only rely on prompts when a TTY is actually present.
   `osh init --yes` skips confirmations.
2. **Never drop databases or overwrite data without explicit user
   confirmation** for that exact operation. Describe the impact and wait
   for a yes first.
3. **Filestore semantics matter.** Database copies link the filestore by
   default — fast, but source and copy share attachment files. An
   independent copy is isolated but slower; skipping the filestore leaves
   the copy without attachments. Pick the mode that matches the task.
4. **Branch = database.** The database name derives from the project
   directory and current git branch. Switching branches switches
   databases — check which one you are on before reading or writing data.
5. **Python compatibility.** Venv runtimes need a Python the project's
   Odoo series supports; old Odoo may also need the `pip` installer for
   source packages that `uv` cannot extract. Both are selectable at init.
6. **Stale-environment warnings** mean the environment predates config or
   source changes — re-running the runtime's init refreshes it.

## Databases follow branches — give each branch its own copy

- Each branch automatically maps to its own database named
  `<project>-<branch>`; branches never share data unless you say so.
- To hand a branch a copy of existing data, switch to the branch first,
  then copy the source database to the branch's derived name —
  `osh db copy <source-db> <project>-<branch>` — or simply run `osh odoo`
  and pick `[c]` (fast, linked filestore) or `[f]` (full independent
  copy) when the missing-database prompt appears. `osh db set` points a
  branch at any existing database instead of copying.
- `osh db exec <cmd>` runs a command inside the branch database's
  environment — `osh db exec psql`, `osh db exec pg_dump ...` — with the
  right `PG*` variables already set. This is the sanctioned way to touch
  data; don't go hunting for credentials or socket paths yourself.

## Git worktrees share one environment

- `git worktree` checkouts borrow the main checkout's entire osh
  environment — `.osh`, virtualenv, Odoo sources, config, Docker stack,
  filestore — so a new worktree needs zero setup: `cd` in and `osh odoo`.
- Each worktree keeps its own branch→database mapping, so parallel agent
  sessions on different branches get separate databases automatically.
- Addon directories left empty in the worktree — typically uninitialized
  submodules — are filled in from the source checkout's copies
  automatically. No `git submodule update` is needed before running.

## Typical assignment flow

1. Confirm `.osh/` exists; if not, initialize the project and a runtime.
2. Preview what would run (`--dry-run`), then run the environment or the
   test suite.
3. For data work, copy an existing database and point the branch at the
   copy before experimenting.
4. Stop leftover processes when done.
