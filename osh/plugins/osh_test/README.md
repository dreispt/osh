# osh_test — `osh test`

Provides the `osh test` command: run Odoo module tests through the active
backend.

## How it works

`osh test` is a thin wrapper around `osh odoo` that chains two invocations:

1. Install/update the requested modules **without** `--test-enable`
   (`-i`/`-u` as appropriate).
2. Run a second Odoo pass **with** `--test-enable` so tests execute on the
   already-installed modules.

This two-phase split avoids Odoo's known pitfall of running tests on
modules that failed to install in the same pass.

## Usage

```bash
osh test my_module            # test one module
osh test mod_a mod_b          # several modules
osh test --all                # all project modules
osh test --dropdb my_module   # recreate the test database first
osh test --current-db         # test on the branch database instead
osh test --tags /mod:Cls.m    # Odoo test tags
osh test --http               # keep the HTTP server running during tests
```

By default tests run on a dedicated database named
`<project>-<branch>-test`, so the branch database is never touched.
`--dropdb` drops the test database first, then installs fresh.
`--no-stop-after-init` keeps the server running after the test pass.

All `osh odoo` options are accepted and forwarded — notably `--target`
(local/venv/docker), `--compose-file`, and `--dry-run` to print the exact
Odoo commands that would run.
