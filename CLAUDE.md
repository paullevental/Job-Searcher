# CLAUDE.md

Project context for agents working in this repo.

**Rules live in [`AGENTS.md`](AGENTS.md), not here.** Hard rules, schema, service
contract, milestones and acceptance checks are all there, and it wins on any
conflict.

Precedence: Paul's global `~/.claude/CLAUDE.md` > `AGENTS.md` > `docs/OFFERRADAR.md`
> this file. One `main` branch only — Paul's instruction on 2026-11-16 supersedes
AGENTS.md §10's branch-per-task line.

---

## M0: what it is

**Goal:** a fresh clone starts the whole stack with one command, and no change
merges without CI passing. No postings are collected — that is M2.

| Deliverable (§11 M0) | State |
|---|---|
| Repo layout per §4 | done |
| Health endpoints | done, verified against real Postgres + Redis |
| Makefile (`up`/`down`/`test`/`lint`/`fmt`) | done |
| `.env.example` | done, 19 vars, sync-checked by a script |
| ADR template | done, `docs/adr/000-template.md` |
| `.gitignore` with `.env` | done, verified a fake key is ignored |
| gitleaks pre-commit hook | config done; needs `brew install pre-commit` + `make hooks` |
| Compose with postgres/redis/agents/workflow/web | **Paul writes** |
| CI running lint + tests | **Paul writes** |
| ADR-001 | **Paul writes** |

### What was built

Two services, the same probe contract in both languages:

- `GET /health` — liveness. Touches no dependency, so a Postgres outage cannot
  make Compose restart a healthy process.
- `GET /ready` — readiness. Checks Postgres and Redis, returns 503 with
  per-dependency detail. This is what `depends_on` should gate on.

`agents/` is Python 3.12 + FastAPI, dependencies via `uv` with a committed
`uv.lock`, ruff + mypy `--strict`. `workflow/` is Java 21 + Spring Boot 3.4, where
`/ready` delegates to Actuator so Spring's own `db` and `redis` indicators do the
work. `web/` is React 19 + Vite 6 + Vitest 3, deliberately a placeholder — the
dashboard is M9.

Three scripts in `scripts/` give mechanical teeth to hard rules that were policy
only: no model SDK imported outside `agents/.../llm/` (§2.8), no banned job-board
domain in source (§2.2), and `.env.example` documenting every variable the code
reads (§9/§12).

Verified: `make lint` clean on all three stacks; `make test` 8 passing (3 pytest,
3 JUnit, 2 Vitest); all three images build and run as non-root; killing Redis
flips `/ready` to 503 while `/health` stays 200; gitleaks catches a fake key.

### Build-environment facts worth knowing

- The Java build runs in a `gradle:8.12-jdk21` container. `gradle` is not
  installed locally and the local JDK is 25 against §5's Java 21, so a fresh
  clone needs only Docker. `make test-workflow`, `make lint-workflow` and
  `make wrapper` all route through it.
- Python is pinned to 3.12 per §5; the local interpreter is 3.14 and `uv` fetches
  3.12.
- Dev dependencies are a PEP 735 `[dependency-groups]` block, not an extra, so
  plain `uv sync` installs them and `uv sync --no-dev` builds the runtime image.
- Every Dockerfile puts its **runtime stage last**, because `docker build` with no
  `--target` builds the final stage.
- `.dockerignore` exists per service, not just at the repo root — Docker reads it
  from the build context root.

### Defects found while building

Five. Four were invisible until something was actually run.

1. **Dockerfile stage order.** All three images were built from the *test* stage;
   `offerradar-agents:dev` failed with `exec: "pytest": not found`.
2. **gitleaks does not detect Anthropic keys.** A well-formed `sk-ant-api03-…`
   scans clean under the default ruleset, so M0's own acceptance check would have
   passed falsely on the only credential this project holds. Fixed with
   `.gitleaks.toml` and three custom rules.
3. That custom rule **printed the secret unredacted** — `--redact` hides the
   capture group, and the group was the variable name. Fixed with `secretGroup = 2`.
4. **Dev deps under `[project.optional-dependencies]`**, which `uv sync` does not
   install, so a fresh clone could not run its own tests.
5. **Missing per-service `.dockerignore`**, so `.env` could have entered an image
   layer, where a later `RUN rm` cannot remove it.

---

## How M0 was built, and why that was wrong

All of M0 — root config, three guard scripts, a Python service, a Java service, a
React app, four docs, Docker verification — was implemented in one conversation
thread. It should have been split across agents per component, with a reviewing
agent behind each.

### Why it wasn't

**I never made the decision.** That is the actual answer. I did not weigh
delegation and reject it; I never evaluated the shape of the work at all. The
sequence was: read "Implement M0", satisfy §3's plan-then-wait rule, ask four
config questions, start writing files. No step in that sequence asks "how many
independent components is this, and who should build them?" so the single-thread
outcome was a default, not a judgement.

**The milestone framing became the work breakdown.** AGENTS.md presents M0 as one
milestone with one bullet list of deliverables, and I treated that as one unit of
execution. A milestone is a unit of *deliverable*. Five components in three
languages is the unit of *execution*. Conflating the two is the root error, and it
will recur on every milestone unless the decomposition question is asked
explicitly before the first file.

**Momentum made each next file cheaper to also do myself.** After writing
`.env.example` I already held the full variable catalogue, so writing
`agents/config.py` myself cost nearly nothing. After that, the probe contract was
in context, so `HealthController.java` was cheap too. Each file I wrote raised the
threshold for stopping to delegate, because the context a subagent would need was
exactly the context I had just accumulated. That is why the thread reached five
workstreams with no decision point: delegating never felt like it would *save*
anything in the moment. It would have — just not to me, and not immediately.

**The constraint I cited afterwards is not the one I acted on.** "Don't spawn
agents unless asked" is a real harness instruction, and `docs/OFFERRADAR.md` §0
rule 3 ("one assistant, one chat at a time") is a real project rule. But I found
rule 3 when asked to justify the choice, not when making it — that is post-hoc
rationalisation. The evidence is that I spawned five agents unprompted for the
AGENTS.md review one turn earlier. So the harness rule plainly is not what
stopped me. The difference was that the review's decomposition was obvious on
sight (four sections, four specialists) while the build's required looking, and I
did not look.

**The check that would have caught it**, before writing anything: *how many
components does this touch, and do they share state?* Answer: five, and three of
them share only a contract. That is the trigger condition for
`superpowers:dispatching-parallel-agents` almost verbatim — "2+ independent tasks
that can be worked on without shared state or sequential dependencies" — and the
skill was never invoked.

### What the split should have been

Not four parallel agents from the start; that breaks, because parts of M0 are
genuinely shared state. `.env.example` is one file every service's config must
agree with, the `/health` + `/ready` contract has to be identical across two
languages, and the guard scripts need the Python package layout.

1. **Sequential, one author:** repo layout, `.gitignore`, `.env.example`, the probe
   contract, Makefile skeleton. This is the interface everything else depends on.
2. **Parallel, three agents:** `agents/`, `workflow/`, `web/`, each handed the
   step-1 contract and forbidden from touching shared files.
3. **A reviewer per service as it lands** — `ecc:python-reviewer`,
   `ecc:java-reviewer`, `ecc:react-reviewer` — not deferred to the end. Review
   timing is where the real damage was: the Dockerfile stage-order bug survived
   authoring, three image builds, and a full `make lint` / `make test` pass, and
   surfaced only when a container was finally started. "The last stage is the
   default target" is a fact about Dockerfiles, not about this repo, so a reviewer
   would have caught it immediately. Defects 1, 3, 4 and 5 are all that kind.
4. **Sequential, one author:** integration. Image builds and the cross-service
   smoke test exercise the seams, so they cannot be parallelised.

### Apply from M1 on

M1 is migrations, which Paul writes, so the unit of work is review, not
generation. From M2 the services diverge enough that steps 2 and 3 are the
default: one agent per component, a reviewer behind each, shared contracts settled
first in a single thread. Ask the decomposition question before the first file,
not after the milestone.

Unresolved: `docs/OFFERRADAR.md` §0 rule 3 forbids parallel agents outright. It
protects Paul's comprehension of code he writes himself, which is not what M0 was
— §3 line 40 designates this scaffolding as generated. Paul should either amend
rule 3 or confirm it applies only to hand-written logic.
