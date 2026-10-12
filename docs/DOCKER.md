# The `docker` runtime — Odoo in Compose, minus the babysitting

**`osh init docker` gives you a containerised Odoo that behaves like a
local one.** The stack comes up once; from then on `osh odoo`, `osh exec`
and `osh db` exec into the running service, so back-to-back commands pay
the container startup cost a single time. The `docker compose`
incantations, the image entrypoint's env vars and the port juggling are
handled for you — and if the project already ships a `compose.yaml` or a
`Dockerfile`, Osh adopts it instead of replacing it.

## Quickstart

```bash
cd my-odoo-project
osh init docker 19.0    # generated odoo:19 + postgres:16 stack
osh odoo                # 'up -d' once, 'compose exec' from then on
```

Requires Docker and the Docker Compose plugin on `PATH`.

## Init

```text
osh init docker [VERSION] [--service odoo] [--command odoo] \
    [--compose-file devel.yaml] [--dockerfile Dockerfile] \
    [--db-service db] [--port 8069] [-e SRC] [-d SRC]
```

Init writes `.osh/docker.toml` recording the service name, container
command, compose file, compose tool, port, version, edition and
Dockerfile; ensures the Odoo sources for the selected edition exist under
`.osh/`; and runs an `odoo --version` smoke test.

| Option                                                | What it does                                                          |
| ----------------------------------------------------- | --------------------------------------------------------------------- |
| `--service`                                           | Compose service name for the Odoo container.                          |
| `--command`                                           | Command to run inside the container (`odoo`, `python3 -m odoo`, ...). |
| `--compose-file`                                      | Compose file to use as-is (`$OSH_COMPOSE_FILE` also works).           |
| `--dockerfile`                                        | Dockerfile to build the Odoo image from (defaults to `./Dockerfile`). |
| `--db-service`                                        | Compose service name for the database container (default `db`).       |
| `--port`                                              | Host port to publish Odoo on (default 8069).                          |
| `-e`, `--enterprise-source` / `-d`, `--themes-source` | Extra source trees for the `ee`/`sh` editions.                        |

The container's Odoo data directory comes from `data_dir` in
`.osh/docker.toml`, the service's `ODOO_DATA_DIR`, or a `/var/lib/odoo` /
`*/data` volume mount — in that order. It is written into the generated
run config and used by `osh db` filestore operations.

`osh db exec` opens a shell or runs commands inside the `db` service
instead of the Odoo one; the postgres image's `POSTGRES_*` variables are
mapped to the usual `PG*` ones.

## Compose file and image resolution

`osh init docker` picks the Odoo container in this order:

- **`--compose-file <path>`** (or `$OSH_COMPOSE_FILE`, or `compose_file`
  in `.osh/docker.toml`): the file is used as-is — a Doodba `devel.yaml`,
  say. Osh never edits it; it is only referenced via
  `docker compose -f <file>` and must already define the service named by
  `--service`.
- **A compose file at the project root** — `compose.yaml`, `compose.yml`,
  `docker-compose.yaml`, `docker-compose.yml` or `devel.yaml`, in that
  order — is auto-detected and used the same way. When several exist the
  first match wins; pass `--compose-file` to pick another.
- **`--dockerfile <path>`** or a `Dockerfile` at the project root: Osh
  generates `.osh/docker-compose.yml` with a `build:` stanza (context is
  the project root) plus the usual `postgres` service, and `osh odoo`
  warns when the image falls behind the recorded build inputs (see
  "Rebuilding images").
- **Neither**: Osh generates `.osh/docker-compose.yml` — a standard
  `odoo:<version>` + `postgres:16` stack that mounts the project at
  `/mnt/extra-addons` and publishes `8069` (or `--port <n>`). It lives
  under `.osh/` so it never clashes with a compose file at the project
  root.

## Stack model

- A project-provided compose file runs under its **natural Compose
  project name** — the same containers `docker compose up` at the
  project root creates. `docker compose ps` shows what Osh manages, and
  `osh stop` downs the project's real stack.
- The generated `.osh/docker-compose.yml` runs under an isolated
  `osh-<dirname>-<hash>` project name instead — its `.osh` directory
  would otherwise give every Osh project the same default name.
- Since Osh runs Odoo via `docker compose exec`, the generated override
  `.osh/docker-compose.osh.yml` adapts foreign stacks to that model: it
  sets the Odoo service command to `sleep infinity` (the file's own
  command is often a running Odoo that would collide on the container's
  HTTP port) and mounts the project root at `/mnt/extra-addons`.
  Database connectivity — the `PG*`/`HOST`/`USER`/`PASSWORD` variables —
  stays the compose file's own responsibility, and its own port mapping
  is what counts for `osh odoo` (an explicit `osh odoo -p <n>`
  republishes the service through the override). Running
  `docker compose up` without the override restores the file's own
  behaviour.

## Execution model

The stack is kept running and reused: every `osh odoo` / `osh exec` /
`osh db` command first runs `docker compose ps --status running
<service>`; when the service is down, `docker compose up -d` brings it
(and its dependencies) up once — images are never rebuilt here (see
"Rebuilding images"). Commands then run via `docker compose exec`, not
one-shot `compose run`.

`exec` bypasses the image entrypoint, so Osh supplies the connection
arguments itself:

- `odoo`/`odoo-bin` invocations are wrapped to map the image's `HOST`,
  `USER`, `PASSWORD`, `PORT` variables to `--db_*` arguments.
- Other commands (e.g. `psql` via `osh db` or `osh exec`) run with those
  variables re-exported as the standard `PG*` names.

Containers are intentionally **left running** after commands exit.
`osh stop` runs `docker compose down` for the project stack;
`osh stop --volumes` also removes the project volumes.

## Rebuilding images

`osh odoo` never rebuilds images — a cold start runs `up -d` as is.
After each build Osh records a fingerprint of the build inputs in
`.osh/cache/env-fingerprints.json`: the service's Dockerfile, the files
at the top of its build context, every `requirements*.txt` inside it,
and the resolved `build:` options. When those differ on a run — an edit,
a new file, a removed one — `osh odoo` warns that the image is stale.

Directories inside the context are not fingerprinted (except for
`requirements*.txt`): what a service COPYs in is usually addon source
code, which the project mount overrides at run time anyway. If your real
build inputs live in subdirectories, list them as context-relative globs
in `build_inputs` in `.osh/docker.toml`.

To rebuild the image, re-run init — on stacks that declare `build:`
services it runs `compose build` when the fingerprint shows the inputs
changed, and updates the record afterwards:

```bash
osh init docker    # rebuild the service images
```

If the warning is a false positive — the inputs changed but the image is
already current — `--fingerprint` only re-records the fingerprint:

```bash
osh init docker --fingerprint    # accept the inputs as built
```

Init updates the image but does not recreate a running container, so a
live stack still needs a restart to run on it:

```bash
osh stop     # down the stack
osh odoo     # cold start reuses the fresh image, then runs Odoo
```

For project-provided compose files, `docker compose build` at the project
root works too.

## Addons paths

The generated Odoo config's `addons_path` is translated to container
paths before being written: directories under the project become
`/mnt/extra-addons/<relative>`; symlinks (e.g. `.osh/odoo` pointing at a
checkout) are resolved on the host first so the mount sees the real
directory.

Sources resolving **outside** the project root — a linked Odoo or
Enterprise checkout shared between projects — cannot be reached through
the project mount. For those, Osh generates
`.osh/docker-compose.osh.yml`, a Compose override adding each one as a
read-only volume under `/mnt/osh-src/<name>-<hash>`, and includes it in
every `docker compose` invocation. It is regenerated on each run, so
changing a link takes effect on the next `osh odoo`/`osh exec` (the stack
is recreated to pick up new mounts). The file is removed when nothing
needs overriding.

Before `up -d`, Osh checks whether the published port is already bound —
the configured/`--port` one on the generated stack, or the `osh odoo -p`
override elsewhere. If the holder is another Osh-managed project, the
error names that project and suggests `osh stop <name>` there or
`osh init docker --port <n>` here.
