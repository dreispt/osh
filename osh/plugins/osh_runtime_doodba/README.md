# osh_runtime_doodba — Doodba Compose runtime

**Already running Odoo on Doodba? Osh speaks it natively.**

Point `osh init doodba` at your existing project and Osh adopts it
as-is — the `devel.yaml` dev stack, `repos.yaml` aggregation, the
`odoo_proxy` port convention, `odoo/custom` build inputs. Nothing is
rewritten, nothing moves: Osh learns Doodba's conventions instead of
asking you to learn Osh's. From then on `osh odoo`, `osh shell`,
`osh db` and `osh stop` drive the same containers your `invoke` tasks
do — plus the extras Osh adds on top: fingerprint-gated image rebuilds
that notice when `build.d` or `dependencies` change, generated branch
configs pre-seeded with `proxy_mode` and the mailhog SMTP, and
`osh stop --volumes` as the `invoke stop --purge` equivalent. And if
you're not on Doodba yet, init offers to scaffold the copier template
for you.

> **Experimental** — the `doodba` runtime is under active development; its
> behaviour and the `.osh/docker.toml` keys it writes may change between
> releases. Do not rely on it for production workflows.

Provides the `doodba` runtime: Odoo runs inside a
[Doodba](https://github.com/Tecnativa/doodba-copier-template) project's
Compose stack, reusing the `docker` runtime's `compose exec` execution
model with Doodba's conventions on top.

## Init

```bash
osh init doodba [--compose-file devel.yaml]
# or: osh init --runtime=doodba
```

When the directory is not already a Doodba project, init offers to
scaffold one with
`copier copy --skip-tasks gh:Tecnativa/doodba-copier-template .`
(requires `copier` on PATH; `--yes` answers `--defaults`). Otherwise it
detects the dev compose file — `docker-compose.yml`, `docker-compose.yaml`
or `devel.yaml`, in that order — and validates that it defines an `odoo`
service.

Init then:

- creates `odoo/auto/addons` (mode 777, like `invoke develop`) and the
  `docker-compose.yml -> devel.yaml` symlink when only `devel.yaml`
  exists;
- writes `.osh/docker.toml` with Doodba conventions (`service = "odoo"`,
  `command = "odoo"`, `db_service = "db"`) plus `build_inputs` covering
  `odoo/custom/{build.d,dependencies,ssh}`;
- writes `.osh/doodba-odoo.conf` (`proxy_mode`, `smtp_server = smtp`,
  `smtp_port = 1025` — the mailhog service) seeding generated branch
  configs;
- builds the Doodba image (`compose build`, fingerprint-gated);
- runs `compose run --rm -T devel-setup` — the git-aggregation that
  populates `odoo/custom/src` — with `UID`/`GID`/`DOODBA_UMASK` set, like
  `invoke git-aggregate`. It is skipped when `odoo/custom/src/odoo`
  already exists;
- runs the usual `odoo --version` smoke test.

Osh-managed source downloads (`ensure_osh_sources`) do not apply:
aggregation owns every checkout, including Enterprise via `repos.yaml`.

## Running

`osh odoo`, `osh shell`, `osh db shell` and `osh stop` behave as under the
`docker` runtime: `compose up -d` starts the stack (odoo sleeps via the
generated Osh override), then `compose exec odoo …` runs the command.

- Odoo is reachable through `odoo_proxy` on `127.0.0.1:<odoo-major>069`
  (e.g. 19069) — `osh odoo -p <n>` republishes the `odoo` service directly
  instead.
- `addons_path` starts with `/opt/odoo/auto/addons`, Doodba's flattened
  addons directory; extra-addons registrations outside the project keep
  their `/mnt/osh-src` mounts.
- Database options resolve from the image's `PG*` env (`PGHOST=db`,
  `PGUSER=odoo`, `PGPASSWORD=odoopassword`), so generated configs need no
  `db_*` entries.
- `osh stop --volumes` runs `compose down --volumes`, removing the
  filestore volume (Doodba's `invoke stop --purge` equivalent).

## Detection

The Odoo version comes from the compose `build.args.ODOO_VERSION`, then
`.copier-answers.yml`. `osh doctor` reports missing Doodba layout pieces
(compose file, `odoo/Dockerfile`, `odoo/custom/src`) with a scaffold hint.
