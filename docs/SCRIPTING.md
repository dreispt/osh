# Scripting and automation

`osh` is designed to be driven by scripts and AI agents, not only typed
interactively. This page documents the contract those callers rely on.

## Exit codes

| Code | Meaning                                                       |
| ---- | ------------------------------------------------------------- |
| 0    | Success                                                       |
| 1    | Error or abort — the operation failed or the user answered no |
| 2    | Usage error — bad flags, missing arguments, invalid input     |
| 3    | Interactive input required — see `--no-input` below           |

Code 3 is only produced on the no-input path: when `--no-input` or
`OSH_NO_INPUT` is set, a command that would prompt exits with 3 instead
of blocking — **no default is substituted**. The error message names
what input was needed, so the caller can re-issue the command with the
relevant flags (e.g. `--yes`, an explicit database name, `--filestore`).

```sh
osh --no-input db copy prod-dump --to-current   # may exit 3 if the
                                              # source is busy and a
                                              # choice is required
OSH_NO_INPUT=1 osh init 19.0                    # same, via environment
```

`--no-input` is a root option but Click consumes it in any position —
`osh db drop --no-input` works the same as `osh --no-input db drop`.
Everything after a literal `--` is passed through untouched.

## Machine-readable output

Read-only commands accept `--json` and print a stable JSON document on
stdout; diagnostics stay on stderr, so `osh ... --json 2>/dev/null`
parses cleanly:

- `osh db show --json` — branch → database resolution and existence
- `osh db list --json` — project databases (plus plugin extras such as
  dangling filestores)
- `osh config show --json` — the project's `.osh/config.toml` sections
- `osh backup list --json` — cached backups
- `osh backup remote list --json` — named backup sources
- `osh stop --list --json` — what `osh stop` would tear down
- `osh init info [--json]` — the resolved environment (roots, runtime,
  Odoo version/edition, database, config paths, interpreter) in one call
- `osh init extra-addons list --json` — registered addon paths

`osh -C DIR` (repeatable, like `git -C`) runs any command as if invoked
from another directory — useful when the caller knows the project path
but is not inside it.

## Interactive prompts

Commands prompt for confirmations, missing-database choices and
runtime selection when a TTY is present. Non-interactive invocations
(pipes, `OSH_NO_INPUT`) either use documented defaults or exit 3 —
never block. Destructive operations still require an explicit opt-in
flag or confirmation; `--no-input` does not bypass them, it makes the
requirement fail fast instead.
