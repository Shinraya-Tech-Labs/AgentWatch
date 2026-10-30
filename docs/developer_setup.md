# Developer Setup Guide

## Environment Requirements
Prepare a Python environment running exactly version 3.12 (newer versions are currently not verified in CI):
```bash
python --version
```

## Repository layout

Three independent applications share the repository root. They are deliberately separate — there is no
root `package.json`, no workspaces, and no shared build — and the differences between them are larger
than they look.

| | `panoptis/` | `frontend/` | `agentwatch-landing/` |
|---|---|---|---|
| what it is | the Python package | the dashboard | the marketing landing page |
| stack | Python 3.12 | Next **14.2.30** | Next **16.2.6** |
| React | — | **18.3.1** | **19.2.4** |
| router | — | **Pages Router** (`pages/`) | **App Router** (`app/`) |
| container | `Dockerfile.api` | `frontend/Dockerfile` | not containerised |
| in `docker-compose` | yes (`api`, `worker`) | yes (`frontend`) | no — deployed separately |
| CI job | lint / type-check / test / bandit | `frontend` job | `landing-build` job |
| npm scripts | — | `dev build start lint type-check test` | `dev build start` |

Run each from its own directory. `npm ci && npm run dev` in `frontend/` gives you the dashboard on
:3000; the same in `agentwatch-landing/` gives you the landing page. They do not share a lockfile and
installing one does not install the other.

### The two Next.js apps are not interchangeable

This is the part worth internalising before you copy anything between them. The dashboard and the
landing page differ on **three independent axes at once**:

- **Next 14 vs Next 16** — different caching defaults, different `next/font` behaviour, different
  config surface.
- **React 18 vs React 19** — Server Components, the `use()` hook, ref-as-a-prop and the rest of the
  React 19 surface exist in the landing page and do not exist in the dashboard.
- **Pages Router vs App Router** — `getServerSideProps` and `next/router` are meaningful in `frontend/`
  and meaningless in `agentwatch-landing/`. `next/navigation`, `async` server components and
  `"use client"` are the reverse.

A component lifted from one into the other will usually *look* like it should work. It will typically
fail on an import that doesn't exist in the target's router, or — worse — type-check and then behave
differently at runtime because the caching or rendering semantics changed underneath it.

If you need shared UI, copy it deliberately and expect to adapt the data-fetching and routing layers.
Do not assume a working component is portable.

### The landing page has less tooling than the dashboard

`frontend/` has `lint`, `type-check` and `test` scripts and a CI job that runs them.
`agentwatch-landing/` has none of those — its CI job builds it and nothing more.

That means **code moved into the landing page loses type checking and linting**. It isn't a licence to
write worse code there; it's a reason to be more careful, because nothing will catch you.

### Configuration stays app-local

Each app owns its own config, lockfile and container assets. The dashboard's Dockerfile lives at
`frontend/Dockerfile`, not at the repository root — the root `Dockerfile.frontend` was removed
precisely so that the dashboard's build context is its own directory.

Keep it that way. Hoisting a config file to the root to "share" it between two apps on different Next
and React majors is how the two dependency graphs start silently constraining each other.


## Installation
1. Clone the repository fork:
   ```bash
   git clone https://github.com/sreerevanth/AgentWatch.git
   ```
2. Navigate and install dependencies:
   ```bash
   cd AgentWatch
   pip install -e ".[dev]"
   ```
3. Run verification tests:
   ```bash
   python -m pytest tests/
   ```
