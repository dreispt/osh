# osh_backend_docker — Docker Compose backend

Provides the `docker` run backend: Odoo and its tools (`psql`, `pg_dump`,
`createdb`, ...) run inside a Docker Compose stack instead of on the host.

## Init

```bash
osh docker init 19.0 [--service odoo] [--command odoo] \
    [--compose-file devel.yaml] [--dockerfile Dockerfile] [--port 8069]
```

Init writes `.osh/docker.toml` recording the service name, container command,
compose file, compose tool, port, version, edition and Dockerfile, ensures
the Odoo sources for the selected edition exist under `.osh/`, and runs an
`odoo --version` smoke test.

The container's Odoo data directory comes from `data_dir` in
`.osh/docker.toml`, the service's `ODOO_DATA_DIR`, or a `/var/lib/odoo` /
`*/data` volume mount — in that order. It is written into the generated
run config and used by `osh db` filestore operations. When the stack
declares none, Odoo's own default applies.

An optional `db_service` key names the Compose service running PostgreSQL
(`--db-service` on init); it defaults to `db`, matching the generated stack
and Doodba. `osh db shell` opens a shell or runs commands in that service
instead of the Odoo one — inside it, the postgres image's `POSTGRES_*`
variables are mapped to the usual `PG*` ones.

## Compose file and image resolution

`osh docker init` picks the Odoo container in this order:

- **`--compose-file <path>`** (or `$OSH_COMPOSE_FILE`, or `compose_file` in
  `.osh/docker.toml`): the file is used as-is — for example a Doodba
  `devel.yaml`. Osh never edits it; it is only referenced via
  `docker compose -f <file>` and must already define the service named by
  `--service`/`service` in `docker.toml`.
- **A compose file at the project root** — `compose.yaml`, `compose.yml`,
  `docker-compose.yaml`, `docker-compose.yml` or `devel.yaml`, in that
  order — is auto-detected and used the same way. When several exist the
  first match wins; pass `--compose-file` to pick another.
- **`--dockerfile <path>`** or a `Dockerfile` at the project root: Osh
  generates `.osh/docker-compose.yml` with a `build:` stanza (context is
  the project root) plus the usual `postgres` service, and `docker compose
up --build` keeps the image current on each cold start.
- **Neither**: Osh generates `.osh/docker-compose.yml` — a standard
  `odoo:<version>` + `postgres:16` stack that mounts the project at
  `/mnt/extra-addons` and publishes `8069` (or `--port <n>`). It lives
  under `.osh/` so it never clashes with a compose file at the project
  root.

## Stack model

- A project-provided compose file runs under its **natural Compose project
  name** — the same containers `docker compose up` at the project root
  creates. `docker compose ps` shows what Osh manages, and `osh docker
stop` downs the project's real stack.
- The generated `.osh/docker-compose.yml` runs under an isolated
  `osh-<dirname>-<hash>` project name instead — its `.osh` directory would
  otherwise give every Osh project the same default name.
- Since Osh runs Odoo via `docker compose exec` (see below), the generated
  override `.osh/docker-compose.osh.yml` adapts foreign stacks to that
  model: it sets the Odoo service command to `sleep infinity` (the file's
  own command is often a running Odoo that would collide on the
  container's HTTP port) and mounts the project root at
  `/mnt/extra-addons`. Database connectivity — the `PG*`/`HOST`/`USER`/
  `PASSWORD` variables — stays the compose file's own responsibility, and
  its own port mapping is what counts for `osh odoo` (an explicit
  `osh odoo -p <n>` republishes the service through the override).
  Running `docker compose up` without the override restores the file's own
  behaviour.

## Execution model

The stack is kept running and reused: every `osh odoo` / `osh shell` /
`osh db` command first runs `docker compose ps --status running <service>`;
when the service is down, `docker compose up -d --build` brings it (and its
dependencies) up once — `--build` is a no-op for image-based stacks and
rebuilds `Dockerfile`-based ones. Commands then run via
`docker compose exec`, not one-shot `compose run`, so back-to-back commands
pay the startup cost once.

`exec` bypasses the image entrypoint, so Osh supplies the connection
arguments itself:

- `odoo`/`odoo-bin` invocations are wrapped to map the image's `HOST`,
  `USER`, `PASSWORD`, `PORT` variables to `--db_*` arguments.
- Other commands (e.g. `psql` via `osh db` or `osh shell`) run with those
  variables re-exported as the standard `PG*` names.

Containers are intentionally **left running** after commands exit.
`osh docker stop` runs `docker compose down` for the project stack.

## Addons paths

The generated Odoo config's `addons_path` is translated to container paths
before being written: directories under the project become
`/mnt/extra-addons/<relative>`; symlinks (e.g. `.osh/odoo` pointing at a
checkout) are resolved on the host first so the mount sees the real
directory.

Sources resolving **outside** the project root — a linked Odoo or
Enterprise checkout shared between projects — cannot be reached through
the project mount. For those, Osh generates
`.osh/docker-compose.osh.yml`, a Compose override adding each one as a
read-only volume under `/mnt/osh-src/<name>-<hash>`, and includes it in
every `docker compose` invocation. It is regenerated on each run, so
changing a link takes effect on the next `osh odoo`/`osh shell` (the stack
is recreated to pick up new mounts). The same override carries the
`sleep infinity` command and `/mnt/extra-addons` mount on foreign stacks
(see "Stack model"); the file is removed when nothing needs overriding.

Before `up -d`, Osh checks whether the published port is already bound —
the configured/`--port` one on the generated stack, or the `osh odoo -p`
override elsewhere; a foreign compose file's own mapping is left to it. If
the holder is another Osh-managed project (identified via Compose labels),
the error names that project and suggests `osh docker stop` there or
`osh docker init --port <n>` here; otherwise it prints a generic
actionable message. In both cases `osh docker stop` is the first suggested
remedy.
