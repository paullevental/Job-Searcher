# Job Searcher

A single-user system that runs nightly, collects co-op and internship postings,
scores them against a resume, finds public hiring-team contacts, and drafts
outreach. It pauses at three human approval gates and **never sends anything**.

**Current Status: Services boot, answer health probes and are checked

---

## Prerequisites

| Tool | Why |
|---|---|
| Docker (with Compose) | Runs Postgres, Redis and all three services. Also runs the Java build, so no local JDK or Gradle is needed. |
| `uv` | Python dependency management for `agents/`. |
| Node 22+ | `web/` development and tests. |
| `pre-commit` | Installs the gitleaks secret hook. `brew install pre-commit` |

## Quickstart

```bash
make env       # create .env from .env.example, then fill in real values
make hooks     # install the gitleaks pre-commit hook
make install   # local dev dependencies for agents and web
make up        # start postgres, redis, agents, workflow, web
```

Then:

- agents — http://localhost:8000/health and /ready, docs at /docs
- workflow — http://localhost:8080/health and /ready
- web — http://localhost:5173

`make down` stops everything and keeps the database. `make down-v` deletes the
database volume too.

## Health probes

Every service answers two endpoints, and the difference matters:

- **`GET /health`** — liveness. The process is up. Touches no dependencies, so a
  Postgres outage never causes Compose to restart a healthy process.
- **`GET /ready`** — readiness. Postgres and Redis are genuinely reachable, with
  per-dependency detail. Returns 503 if any check fails. This is what
  `depends_on: condition: service_healthy` should gate on.

## Commands

`make help` lists everything. The ones that matter:

```bash
make test      # pytest + JUnit + Vitest, all offline
make lint      # repo guards, then ruff/mypy, Spotless, ESLint/Prettier/tsc
make fmt       # format everything in place
make check     # just the repo rule guards
```

### Repo guards

Three of the hard rules in `AGENTS.md` §2 were policy with no enforcement, so
`make check` enforces them mechanically:

| Script | Rule |
|---|---|
| `scripts/check_llm_chokepoint.py` | §2.8 — no model SDK imported outside `agents/.../llm/`, which is the single place that checks the budget and sets `max_tokens`, timeouts and retries |
| `scripts/check_banned_domains.py` | §2.2 — no reference to the five banned job boards |
| `scripts/check_env_example.py` | §9/§12 — `.env.example` documents every variable the code reads |

The first two are source greps and so are defeated by string construction; the
durable control is egress filtering at the Docker layer. See `docs/TODO.md`.

## Layout

```
agents/     Python 3.12 — FastAPI, LangGraph supervisor, scout/matcher/recon/scribe
workflow/   Java 21 — Spring Boot; owns the schema (Flyway), schedules, approvals
web/        React + TypeScript + Vite — talks only to workflow
api/        openapi.yaml, the contract between workflow and agents (M8)
scripts/    repo rule guards
docs/       OFFERRADAR.md (design), adr/, LEARNING_LOG.md, TODO.md
```

## Architecture

`workflow` (Java) owns schedules, approvals, the run state machine, the REST API
for `web`, and the database schema. `agents` (Python) owns the LangGraph graph and
all agent logic; it reads and writes data tables but never changes the schema.
`web` talks only to `workflow`.

The nightly run funnels deliberately, because the expensive stage must stay small:

```
Quartz 02:00 → Scout → dedup/staleness → prefilter
             → stage 1: embed + rank          (local, free)
             → Gate 1: pick postings          (human)
             → stage 2: Claude scoring + Recon on selected postings only
             → Gate 2: fit + contacts         (human)
             → Scribe → Gate 3: drafts        (human) → done
```

Full reasoning is in [`docs/OFFERRADAR.md`](docs/OFFERRADAR.md). Agent-facing
rules, schema and acceptance checks are in [`AGENTS.md`](AGENTS.md).

## Secrets

`.env` is gitignored; `.env.example` holds placeholders only. gitleaks runs as a
pre-commit hook. Only the `agents` service ever reads a model key — `workflow` and
`web` never receive one, and anything prefixed `VITE_` is compiled into the
browser bundle and therefore public.

`pre-commit` hooks are client-side and `--no-verify` bypasses them by design, so
the real gate is the same scan in CI with branch protection making it required.
That setup is still outstanding; see `docs/TODO.md`.
