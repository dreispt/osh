# Parallel AI agents need parallel checkouts

Using AI coding agents, to be able to have them work in parallel
in the same project, you will arrive at the conclusion that
more than one checkout is needed.

An agent session fixes the portal count bug, another rewrites the customer
dedup logic, a third reviews a colleague's PR — and each wants a working
copy of the project it can safely modify and run.

In Odoo development, "a working copy" is heavy. A checkout is not just the
addon directories: it drags along Odoo and enterprise source clones, a
virtualenv, generated configs, a database, maybe a Docker stack. So how
do you give every agent session its own checkout without paying that
cost several times over?

## The naive options hurt

**One shared checkout.** All sessions run `git switch` in the same
work on cerefully designed independent tasks, so that
your agents don't stomp on each other's uncommitted changes and `odoo` runs.
It serializes enything else, and even with this care when it comes to create
bernches and pull requests confusion sets in.

**Full clone per session.** `git clone` is easy but heavy.
Each clone needs its Odoo sources fetched or cached, its `.venv` built,
its config written. Setup time dominates the actual work, and every copy
fights for the same databases unless you're careful to keep track
of what agent is using what database.

The real need: **many working trees, one shared environment.**

## Git worktrees: half the answer

`git worktree` gives you parallel checkouts of one repository — separate
directories, separate branches, shared object store:

```console
$ git worktree add ../myproject-fix-portal fix/portal-count
Preparing worktree (checking out 'fix/portal-count')
```

Each worktree is a first-class checkout: its own branch, its own
uncommitted files, its own build. When a session ends,
`git worktree remove` cleans up. This is exactly the shape agent
sessions want — but it only solves the _files_ problem.

## The other half: the environment

A fresh worktree is just source code. There's no `.osh` in it — no
virtualenv, no Odoo sources, no `odoo.conf`, no database. Until now,
`osh` run inside a worktree would shrug: it walks _up_ from the git root
looking for `.osh`, and a worktree sits _beside_ its repository, not
below it. "Not inside an Osh project."

As of 1.14, `osh` understands the worktree relationship and borrows the
source repository's environment:

- **The environment is shared.** `.osh`, `.venv`, Odoo/enterprise/theme
  sources, `config.toml`, the Docker stack, filestore paths — all
  resolve to the main repository's copy. Setup cost for a new worktree:
  zero.
- **The branch is yours.** The worktree's own branch drives osh's
  per-branch database naming, so `fix/portal-count` gets
  `myproject-fix-portal-count` — parallel sessions never touch the
  same database.
- **Uninitialized submodules just work.** A fresh worktree's submodule
  directories are empty; `osh` fills them in from the source checkout's
  copies, after the worktree's own addon dirs. No `git submodule update`
  required to run.
- **Docker projects are covered.** Worktree addon dirs outside the
  repository are mounted read-only into the shared stack.

## A working session

```console
$ git worktree add /tmp/myproj-portal fix/portal-count
$ cd /tmp/myproj-portal
$ osh odoo
```

Meanwhile, in another shell (or another agent):

```console
$ git worktree add /tmp/myproj-dedup fix/customer-dedup
$ cd /tmp/myproj-dedup
$ osh apps update      # fingerprints and updates only changed modules
```

Both sessions drive the same environment — same Odoo code, same venv,
same stack — but run against `myproject-fix-portal-count` and
`myproject-fix-customer-dedup` respectively. A nonexistent branch
database triggers the usual prompt, including the option to copy the
last used one.

What's checkout-local vs. shared:

| Checkout-local (the worktree)     | Shared (the env root)                |
| --------------------------------- | ------------------------------------ |
| branch → database name            | `.osh`, `config.toml`, `docker.toml` |
| addon directories (own + fill-in) | `.venv`, Odoo sources, themes        |
| working tree, uncommitted changes | Docker stack, caches, filestore      |

## Edge cases worth knowing

- **A worktree can opt out.** `osh init` inside a worktree creates its
  own `.osh` — from then on it's an independent environment.
- **Shadowing is per directory.** If a worktree dir has _any_ content,
  it shadows the source counterpart as a whole — the worktree's branch
  stays authoritative and never mixes module sets.
- **Detached HEAD** resolves the database as `<project>-default`.
- **One port, one stack.** Two worktrees serving Odoo share the
  configured port — same as two branches ever did.

## Try it

```console
$ git worktree add ../myproj-wip feature/wip
$ cd ../myproj-wip
$ osh odoo
```

That's it. Your agents — and your own side branches — get parallel,
runnable checkouts for the price of a directory.

Happy coding!
