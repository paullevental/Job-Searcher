# AGENTS.md: OfferRadar

Instructions for any coding agent working in this repo. Read all of it before your first change. The human developer is Paul. The full design rationale is in `docs/OFFERRADAR.md`; you do not need it to work, but read the relevant section if a rule here is unclear.

**Current milestone: M1** ← Paul updates this line. Work only on this milestone.

---

## 1. What this project is

A single-user system that runs nightly, collects co-op/internship postings, scores them against Paul's resume, finds public hiring-team contacts, and drafts outreach. It pauses at three human approval gates. It never sends anything.

---

## 2. Hard rules (never break)

1. **Never send email, messages or applications** from any code path. Drafts are stored with status `draft`; only Paul marks them `sent_manually`.
2. **Never scrape or call LinkedIn, Indeed, Jobright, Handshake or Wellfound.** No code may reference their domains except the manual-add form.
3. **Respect robots.txt** and per-domain rate limits on every outbound request. Use a descriptive User-Agent.
4. **No guessed or inferred email addresses.** Contacts store `name`, `title`, `source_url` only. Drop any contact without a `source_url`.
5. **No live network or LLM calls in tests.** Use recorded fixtures.
6. **Never edit a migration that has been merged.** Add a new one.
7. **Never commit secrets.** Read config from environment variables; keep `.env.example` up to date.
8. **Every LLM call goes through the shared client** that logs to `llm_calls` and checks the run budget first.
9. **API keys never reach the browser, logs, Docker images, CI output or git.** Only the `agents` service reads model keys. See §9.
10. **Every LLM call sets `max_tokens`, a timeout, and a retry limit.** No unbounded loops around paid calls.
11. **Claude (stage 2) only runs on postings selected at Gate 1.** Never send all postings to a paid model.

---

## 3. Working with Paul: who writes what

This is a learning project. **Paul writes the core logic himself.** Each milestone in §11 lists `Paul writes` items.

For anything listed under `Paul writes`:
- Do **not** write the implementation, even if asked casually. If Paul explicitly says "write it for me" for a specific item, confirm once, then do it and note it in `docs/LEARNING_LOG.md`.
- You **may**: explain concepts, point to docs, give hints or pseudocode outlines of no more than a few lines, write failing tests that define the expected behavior (only if Paul asks), and review his code after he writes it.
- When reviewing, explain *why* something is a problem. Don't just rewrite it.

You **may write freely**: Dockerfiles, Compose config, CI workflows Paul has already drafted, build files (`pyproject.toml`, `build.gradle`, `package.json`), entity/DTO/Pydantic classes from the schema, CRUD controllers, generated API clients, CSS/styling, test fixtures, and README sections other than "what I'd do differently".

Before writing more than ~100 lines in one go, describe the plan and wait for Paul's OK.

Don't start work belonging to a later milestone. If you notice something needed later, add it to `docs/TODO.md`.

---

## 4. Repository layout

```
offerradar/
├── AGENTS.md
├── docker-compose.yml
├── .env.example
├── .github/workflows/ci.yml
├── api/openapi.yaml            # contract between workflow/ and agents/ (source of truth)
├── agents/                     # Python 3.12 service
│   ├── pyproject.toml
│   ├── src/offerradar_agents/
│   │   ├── api/                # FastAPI app
│   │   ├── graph/              # LangGraph supervisor + state
│   │   ├── scout/              # adapters per source
│   │   ├── dedup/
│   │   ├── matcher/
│   │   ├── recon/
│   │   ├── scribe/
│   │   ├── llm/                # shared Claude client, cost logging, budget
│   │   └── db/                 # SQLAlchemy models (read/write only; no DDL)
│   ├── prompts/                # versioned prompt files: score_v1.md, ...
│   └── tests/ (fixtures/ inside)
├── workflow/                   # Java 21 Spring Boot service
│   ├── build.gradle
│   └── src/main/{java,resources/db/migration}   # Flyway owns the schema
├── web/                        # React + TypeScript + Vite
└── docs/
    ├── OFFERRADAR.md           # design + learning roadmap (for Paul)
    ├── adr/                    # NNN-title.md
    ├── LEARNING_LOG.md
    └── TODO.md
```

---

## 5. Stack

| Area | Use | Don't use |
|---|---|---|
| Agent service | Python 3.12, FastAPI, LangGraph (+ Postgres checkpointer), httpx (async), Playwright (only for JS-rendered pages), SQLAlchemy 2, Pydantic v2 | requests, Selenium, CrewAI/AutoGen |
| Embeddings | Ollama with `nomic-embed-text` (768 dims; use `search_document:` / `search_query:` prefixes). Alternative: `bge-small` (384 dims). Behind `EmbeddingProvider` | Paid embedding APIs (unless Paul decides after M4's eval) |
| LLM | Anthropic SDK behind `LLMProvider`, selectable by config: `anthropic` (default) or `ollama` (local, for dev and comparison). Scoring/classification: `claude-haiku-5-5`. Recon/Scribe: `claude-sonnet-5-5`. Tool use with JSON schemas for structured output, prompt caching for resume/system context, Message Batches API for nightly scoring | Free-text JSON parsing, calling an SDK directly outside the provider module |
| Workflow service | Java 21, Spring Boot 3, Spring Data JPA, Quartz (JDBC job store), Bean Validation | `ddl-auto=update`, `@Scheduled` for the nightly run |
| Database | PostgreSQL 16 + pgvector, Flyway migrations (plain SQL) | Separate vector DB |
| Events / locks | Redis 7: Streams with consumer groups, rate-limit buckets, run lock | Kafka, RabbitMQ |
| Frontend | React, TypeScript (strict), Vite, TanStack Query, Vitest + Testing Library | Next.js, Redux |
| Tests | pytest + respx, JUnit 5 + Testcontainers, Vitest | Live network in tests |
| Secrets | `.env` (gitignored) + `.env.example`; gitleaks pre-commit hook; GitHub secret scanning + push protection; GitHub Actions secrets | Keys in code, images, build args or frontend |
| Tooling | ruff + mypy (Python), Spotless/google-java-format (Java), ESLint + Prettier (web) | |

Inter-service calls: REST/JSON per `api/openapi.yaml`. Generate clients from the spec; don't hand-write request code.

---

## 6. Architecture contract

- `workflow` (Java) owns: schedules, approvals, the run state machine, the REST API for `web`, and the database schema.
- `agents` (Python) owns: the LangGraph graph and all agent logic. It reads and writes data tables but never changes the schema.
- `web` talks only to `workflow`.

**Endpoints on `agents`:** `POST /runs` (body: `budget_usd`; header `Idempotency-Key`), `POST /runs/{id}/resume` (body: `gate`, decisions; header `Idempotency-Key`), `GET /health`.

**Event:** when the graph hits a gate it calls `interrupt()` and publishes to Redis stream `runs.events`: `{type: "run.awaiting_approval", run_id, gate}`. `workflow` consumes with consumer group `workflow`, acknowledges after the DB update commits, and also polls every 5 min for runs in `awaiting_*` as a fallback.

**Run lock:** Redis key `lock:run` with TTL; a second `POST /runs` while locked returns 409.

**Nightly flow:** Quartz 02:00 → `POST /runs` → Scout → dedup/staleness → prefilter → **stage 1: embed + rank (local, free)** → **Gate 1 (pick postings for analysis; top ~20 shown; optional auto-forward of top `AUTO_FORWARD_TOP_N`)** → **stage 2: Claude scoring** + Recon on selected postings only → **Gate 2 (fit + contacts)** → Scribe → **Gate 3 (drafts)** → done.

**Run states:** `running → awaiting_gate_1 → running → awaiting_gate_2 → running → awaiting_gate_3 → done`; any state → `failed` or `budget_exhausted`. Reject any other transition with HTTP 409.

---

## 7. Data model

Flyway SQL in `workflow/src/main/resources/db/migration` (V001-V008). Embedding dimension: 768 for `nomic-embed-text` (change in one constant if the model changes; store `embedding_model` and re-embed on change, never mix models). `docs/schema.md` holds a longer write-up — ER diagram, per-table normal-form analysis, the query behind each index — but it is a local working document and not tracked in git, so this section is the authoritative copy.

Three conventions apply throughout. Money is `numeric`, never float, because `spent_usd` accumulates by repeated addition into a hard cap. Status-like columns are `text` with a `CHECK`, never a native enum, because Flyway is append-only and replacing a constraint is easier than `ALTER TYPE ... ADD VALUE`. Mutable tables carry `created_at` and `updated_at`, the latter maintained by the shared `set_updated_at()` trigger in V001; tables whose rows are immutable facts (`posting_sightings`, `resume_chunks`, `match_analyses`, `llm_calls`) carry `created_at` only. `approvals` carries `decided_at` alone, because there the row *is* the decision and a second timestamp would always equal it.

| Table | Columns |
|---|---|
| `companies` | `id`, `name`, `normalized_name` (unique), `domain`, `source` (`yc`/`manual`/`github_list`/`ats_discovery`) |
| `ats_boards` | `id`, `company_id` FK, `provider` (`greenhouse`/`lever`/`ashby`/`workable`), `board_token`, `last_polled_at`, `etag`, `consecutive_misses`; unique `(company_id, provider, board_token)` |
| `postings` | `id`, `company_id` FK, `title`, `normalized_title`, `location`, `remote` bool, `term`, `canonical_url`, `dedup_key` (unique), `description`, `description_hash`, `embedding vector(768)`, `embedding_model`, `status` (`open`/`stale`/`closed`/`merged`), `merged_into_id` self-FK, `first_seen_at`, `last_seen_at`, `missed_polls` int |
| `posting_sightings` | `id`, `posting_id` FK, `source`, `source_url`, `seen_at`; unique `(posting_id, source, source_url)`. One row per distinct place a posting was seen, not an event log: a repeat sighting refreshes `seen_at` through `ON CONFLICT`, which is what makes Scout idempotent |
| `resume_chunks` | `id`, `version`, `kind` (`bullet`/`project`/`course`), `ordinal`, `text`, `embedding vector(768)`, `embedding_model`; unique `(version, ordinal)` |
| `matches` | Stage 1 only: `id`, `posting_id` FK unique, `similarity` real, `embedding_model`, `resume_version`, `selected_for_analysis` bool |
| `match_analyses` | Stage 2 only: `id`, `posting_id` FK, `description_hash`, `prompt_version`, `resume_version`, `model`, `provider` (`anthropic`/`ollama`), `score` smallint 0-100, `matched_skills` jsonb, `missing_skills` jsonb, `why_fit`; unique on `(posting_id, description_hash, prompt_version, resume_version, model, provider)` |
| `contacts` | `id`, `posting_id` FK, `name`, `title`, `source_url` NOT NULL, `confidence`, `deleted_at`. Soft delete, because `outreach_drafts` references this row and a `sent_manually` draft is the only human-authored record in the system |
| `outreach_drafts` | `id`, `contact_id` FK, `body`, `edited_body`, `status` (`draft`/`approved`/`sent_manually`/`discarded`) |
| `agent_runs` | `id`, `started_at`, `finished_at`, `status` (`running`/`awaiting_gate_1`/`awaiting_gate_2`/`awaiting_gate_3`/`done`/`failed`/`budget_exhausted`), `budget_usd` numeric, `spent_usd` numeric, `llm_call_count`, `langgraph_thread_id` (unique; the only join key to the LangGraph checkpointer) |
| `approvals` | `id`, `run_id` FK, `gate` (1/2/3), `item_type` (`posting`/`contact`/`draft`), `item_id`, `decision` (`approved`/`rejected`), `decided_at`; unique `(run_id, gate, item_type, item_id)`. `item_id` is a polymorphic reference, so it carries no foreign key and the application must verify the target exists |
| `llm_calls` | `id`, `run_id` FK, `agent` (`supervisor`/`scout`/`matcher`/`recon`/`scribe`), `model`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `cost_usd` numeric, `latency_ms`, `prompt_version`, `created_at` |

**Why stage 1 and stage 2 are separate tables.** Stage 2 can legitimately produce more than one row for the same posting, because a score is only meaningful relative to the inputs that produced it: a different `description_hash`, `prompt_version`, `resume_version`, `model` or `provider` is a different result, not a correction of the earlier one. (§8's skip rule keys on a subset of those; which subset is an open M5 question.) A single `matches` table with `UNIQUE (posting_id)` cannot hold M5's required comparison of two prompt versions across two providers, and it forces `similarity` — which is determined by `posting_id` alone — to repeat identically across every scoring row for that posting. Formally: `UNIQUE (posting_id)` would make `posting_id` a candidate key of the combined table while it is also a proper subset of the six-attribute key stage-2 data needs, and a non-trivial dependency whose left side is a proper subset of a candidate key is a 2NF violation.

`postings` carries four CHECKs that later milestones lean on: `status = 'merged'` holds if and only if `merged_into_id` is set; a posting cannot be merged into itself; an `embedding` cannot exist without its `embedding_model`; and `description` and `description_hash` are either both present or both absent, so the memoization key cannot drift from the text it was computed over. `resume_chunks` repeats the embedding pairing. `matches.similarity` is constrained to [-1, 1] and `contacts.confidence` to [0, 1]. Every CHECK and every multi-column UNIQUE is explicitly named (`ck_*`, `uq_*`) so a later migration can alter one by name instead of by declaration order.

Indexes: HNSW with `vector_cosine_ops` on both `embedding` columns, matching §8's cosine ranking; unique on `postings.dedup_key`; FK indexes on `postings(company_id)`, `postings(merged_into_id)`, `outreach_drafts(contact_id)` and `llm_calls(run_id)`; partial `postings(last_seen_at) WHERE status = 'open'` for the staleness sweep; partial `contacts(posting_id) WHERE deleted_at IS NULL`; `ats_boards(last_polled_at)` for the poll queue; `agent_runs(status)` for the reconciler. A plain index on an FK already covered by the leading column of a UNIQUE constraint is not created, and neither is the partial `postings(status) WHERE status = 'open'` this section previously specified. Postgres satisfies an equality lookup from the leading column of a multicolumn unique index just as well as a dedicated index would; and a single low-cardinality partial index with no co-predicate matches no query in §8, because every real read wants open rows ordered or filtered by something else, which `postings(last_seen_at)` already covers.

Deletes from `postings` cascade to derived rows (`posting_sightings`, `matches`, `match_analyses`) and are restricted by `contacts`. The asymmetry is deliberate: derived rows can be recomputed, whereas a contact names a real person and has human-authored drafts hanging off it. Merging a duplicate posting is an `UPDATE` setting `status = 'merged'` and `merged_into_id`, never a `DELETE`. One level up, `companies` cascades to `ats_boards` but is restricted by `postings`, on the same reasoning: a board is a polling detail, a posting is history.

---

## 8. Component specs

### Scout
- One adapter per source implementing `fetch() -> list[RawPosting]` and `normalize(raw) -> Posting`.
- Sources: SimplifyJobs and Ouckah README tables (parse only rows changed since the last processed commit), Greenhouse `boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`, Lever `api.lever.co/v0/postings/{company}?mode=json`, Ashby `api.ashbyhq.com/posting-api/job-board/{board}`, Workable public jobs endpoint.
- Use ETag / `If-None-Match` where supported. Exponential backoff with jitter, max 4 retries. A source returning 0 results or failing is recorded as an alert, not a crash.
- Upserts must be idempotent: running Scout twice on the same input creates no new rows.

### Dedup and staleness
1. Canonicalize URLs: drop `utm_*`, `gh_src`, `ref`, fragments; lowercase host; follow redirects.
2. `dedup_key = sha256(normalized_company | normalized_title | term)`. Match → add a `posting_sightings` row.
3. Fuzzy fallback (same company, open postings only): similarity above `DEDUP_MERGE_THRESHOLD` merges; between `DEDUP_REVIEW_THRESHOLD` and merge threshold flags "possible duplicate". Thresholds are config values chosen from the labeled set in `agents/tests/fixtures/dedup_pairs.json`.
4. Status: ATS posting missing for 2 consecutive polls → `closed`. GitHub 🔒 or row removed → `stale`, confirmed against ATS. Repo-only postings: weekly URL check; 404 or redirect to a generic careers page → `closed`. `last_seen_at` > 21 days → `stale`.
5. Only `open` postings go to Matcher.

### Matcher (two-stage funnel)
- **Stage 0, prefilter:** rules only (term, location, seniority words, citizenship/clearance keywords).
- **Stage 1, embed + rank (free):** embed resume chunks once per `resume_version`; embed each posting once on first sight. `rank_postings(k=20)` scores each open posting by cosine similarity to resume chunks (formula chosen by Paul in M4, e.g. mean of top-3 chunk similarities), writes `matches.similarity`, and sends the top `GATE1_SHORTLIST_SIZE` (default 20) to Gate 1 with their nearest resume chunks.
- **Gate 1:** Paul sets `selected_for_analysis`. If `AUTO_FORWARD_TOP_N` > 0 (default 0), that many top postings are selected automatically.
- **Stage 2, Claude scoring:** only `selected_for_analysis = true`. Results go to `match_analyses`, not `matches` (§7). Skip if a `match_analyses` row exists for the same `description_hash`, `prompt_version`, `resume_version`. That row also records `model` and `provider`; whether those two belong in the skip key is an open M5 decision, so do not assume either answer. Retrieve top-k resume chunks → score via Batches API.
- Output schema (via tool use): `{score: int 0-100, matched_skills: string[], missing_skills: string[], why_fit: string (≤2 sentences)}`.
- Prompts live in `agents/prompts/` and carry a version; store `prompt_version` with every result.

### Supervisor (LangGraph)
- Typed state object; routing is deterministic code. Call the LLM only for anomalies (e.g. a source returned 0 results).
- Check budget before each LLM call; on exhaustion set run status `budget_exhausted` and stop cleanly.
- Every node must be safe to re-run after a crash.

### Recon
- Inputs: approved postings only. Sources: company team/about pages, engineering blog author pages, public GitHub org members.
- Playwright only when plain HTTP returns no usable content.
- Extract `{name, title, source_url}`; drop entries without `source_url`. Max 3 contacts per posting.
- Soft-delete contacts when their posting becomes `closed`: set `deleted_at`, never `DELETE`. `outreach_drafts` references `contacts`, and a `sent_manually` draft is the only human-authored record in the system.

### Scribe
- Inputs: approved contacts. Use Paul's example messages in `agents/prompts/scribe_examples/` as few-shot examples.
- ≤120 words. `lint_draft` rejects drafts that exceed the limit or make claims not traceable to a `resume_chunks` row.

### Providers
- `EmbeddingProvider.embed(texts) -> list[vector]` and `LLMProvider.complete(...)`, selected by `EMBEDDING_PROVIDER` / `LLM_PROVIDER`. No other module imports a model SDK.

### LLM client
- Single module `agents/src/offerradar_agents/llm/`. Every call: check run budget and `MAX_LLM_CALLS_PER_RUN` → call with `max_tokens`, timeout and caching headers → compute cost → insert `llm_calls` row → update `agent_runs.spent_usd` and `llm_call_count`.
- Retries: exponential backoff with jitter, max `LLM_MAX_RETRIES` (default 3), only on rate-limit, overload, timeout and 5xx errors. Never retry 400/401/403.
- On budget or call-limit exhaustion: set run status `budget_exhausted`, stop cleanly, report remaining work.

---

## 9. Secrets and cost controls

**Secrets**
- `.env` is in `.gitignore`; `.env.example` lists every variable with placeholder values. Keep it in sync.
- gitleaks runs as a pre-commit hook and in CI. Do not bypass it (`--no-verify`). If it flags something, stop and tell Paul.
- Only `agents` reads `ANTHROPIC_API_KEY` (and any other model key). `workflow` and `web` never receive model keys. No key or provider URL with credentials in frontend code.
- Never log headers, config objects or exceptions that include keys. Use a redacting log filter for anything matching key patterns.
- Docker: pass keys via `env_file`/environment at run time. Never `COPY .env`, never put keys in `ARG`/`ENV` in a Dockerfile.
- CI: tests don't need a real key (fixtures). If a workflow ever needs one, use GitHub Actions secrets and never `echo` it.
- Separate keys for dev and prod (`.env` locally, server env file with `chmod 600` in prod).
- If you see a key anywhere it shouldn't be (code, logs, history), stop and tell Paul to revoke it. Do not try to "clean it up" quietly.

**Cost limits (config, with defaults)**

| Variable | Default | Purpose |
|---|---|---|
| `RUN_BUDGET_USD` | 0.50 | Hard per-run spend cap |
| `MAX_LLM_CALLS_PER_RUN` | 100 | Second guard against loops |
| `LLM_MAX_TOKENS_SCORE` / `_DRAFT` / `_RECON` | 400 / 400 / 800 | `max_tokens` per call type |
| `LLM_TIMEOUT_SECONDS` | 60 | Per-call timeout |
| `LLM_MAX_RETRIES` | 3 | Retry cap |
| `GATE1_SHORTLIST_SIZE` | 20 | Postings shown at Gate 1 |
| `AUTO_FORWARD_TOP_N` | 0 | Top N sent to stage 2 without manual pick |
| `LLM_PROVIDER` / `EMBEDDING_PROVIDER` | `anthropic` / `ollama` | Provider switch |

In development, default `LLM_PROVIDER=ollama` or run on a small sample of postings. Provider-side caps (Console workspace spend limit, prepaid credits) are Paul's job; don't touch account settings.

---

## 10. Conventions

- **Branches and PRs:** one branch per milestone task, PR into `main`, CI must pass. Conventional commit messages (`feat:`, `fix:`, `test:`, `docs:`, `chore:`).
- **Tests:** every bug fix gets a test. Python tests use fixtures under `agents/tests/fixtures/`. Java integration tests use Testcontainers Postgres.
- **ADRs:** `docs/adr/NNN-short-title.md` with sections Context, Options, Decision, Consequences. Paul writes them; you may create the empty file from the template.
- **Logging:** structured JSON; include `run_id` on every log line inside a run.
- **Config:** all thresholds, budgets and model names come from environment/config, never hard-coded in logic.
- **Commands:** keep a `Makefile` (or `justfile`) at the root with `up`, `down`, `test`, `lint`, `fmt`. Use these commands when you check your own work.

---

## 11. Milestones

Work only on the **current milestone** (top of file). A milestone is done when every acceptance check passes in CI or is shown to Paul.

### M0. Foundations
- **Deliver:** repo layout from §4; Compose with `postgres` (pgvector image), `redis`, `agents`, `workflow`, `web`; health endpoints; CI running lint + tests for all three apps; Makefile; `.env.example`; ADR template; `.gitignore` with `.env`; gitleaks pre-commit hook and CI step.
- **Paul writes:** `docker-compose.yml`, `.github/workflows/ci.yml`, ADR-001.
- **Accept:** fresh clone + `make up` → all services healthy; CI green; a PR with a failing test is blocked; committing a fake key is blocked by gitleaks.

### M1. Data model
- **Deliver:** Flyway migrations for all tables and indexes in §7; seed script for a few companies.
- **Paul writes:** every migration SQL file.
- **Accept:** migrations apply from empty in CI; test proves duplicate `dedup_key` insert fails.

### M2. Scout
- **Deliver:** adapters for SimplifyJobs, Ouckah, Greenhouse, Lever; normalization; idempotent upsert; rate limiting; retries; alerts for failing sources; fixtures.
- **Paul writes:** README table parser, `normalize`, upsert, retry/backoff.
- **Accept:** running Scout twice on fixtures adds zero rows the second time; failing-source test shows backoff + alert; suite runs offline.

### M3. Dedup and staleness
- **Deliver:** canonicalization, `dedup_key`, fuzzy fallback (string similarity first), sightings, status transitions, labeled pair set, precision/recall report in CI.
- **Paul writes:** all of it, including labeling pairs.
- **Accept:** CI prints precision/recall; test proves one missed poll doesn't close a posting.

### M4. Retrieval
- **Deliver:** Ollama service in Compose; `EmbeddingProvider`; resume chunking + embedding; posting embedding on first sight; pgvector storage; `rank_postings(k)`; `retrieve_resume_context(text, k)`; ranking and retrieval evals; fuzzy dedup upgraded to embeddings.
- **Paul writes:** chunking, ranking formula, retrieval function, eval scripts; the embedding-model comparison; the RAG vs. whole-resume experiment.
- **Accept:** ranking recall (Paul's top-rated postings in top 20) and retrieval recall@k reported; ADR with comparison numbers; no paid API calls anywhere in stage 1.

### M5. Matcher
- **Deliver:** prefilter; Gate 1 selection + `AUTO_FORWARD_TOP_N`; `LLMProvider` (Anthropic + Ollama); scoring via tool use; prompt caching; batch scoring; memoization; shared LLM client with all §9 cost controls; eval set of 30 rated postings.
- **Paul writes:** prefilter rules, provider interface, scoring prompt + schema, cost calculation and budget guards, eval script.
- **Accept:** eval results for ≥2 prompt versions and both providers; cost per night with funnel vs. scoring all postings; tests prove the budget cap, call cap and retry cap each stop a run; no unselected posting reaches stage 2.

### M6. Orchestration
- **Deliver:** (a) a minimal hand-written orchestrator in `agents/src/offerradar_agents/graph/handrolled.py`; (b) the LangGraph graph with checkpointer, three `interrupt()` gates, budget checks, FastAPI `POST /runs` and `/resume`.
- **Paul writes:** the hand-rolled orchestrator, graph definition, state type, routing, budget guard.
- **Accept:** test kills the process mid-run and the run resumes with no lost or repeated steps.

### M7. Workflow service
- **Deliver:** JPA entities, REST API for runs/postings/approvals/contacts/drafts, run + approval state machines, Quartz nightly job with JDBC store and misfire policy, error handling, Testcontainers tests.
- **Paul writes:** state machines and their service layer, transaction boundaries, Quartz job, illegal-transition tests.
- **Accept:** illegal transitions return 409; Quartz triggers a run (1-minute schedule in a test profile); no N+1 queries in SQL logs for list endpoints.

### M8. Service integration
- **Deliver:** `api/openapi.yaml`, generated clients, Redis Streams publisher/consumer, polling fallback, idempotency keys, timeouts/retries, run lock.
- **Paul writes:** the OpenAPI spec, stream consumer + ack logic, idempotency handling, lock.
- **Accept:** duplicate `/resume` with same key has one effect; killing either service mid-run loses nothing; dropped event is caught by the fallback.

### M9. Dashboard
- **Deliver:** React app: run overview, Gate 1/2/3 review screens, draft editor, cost panel, manual-add form; keyboard shortcuts; optimistic updates with rollback; component tests.
- **Paul writes:** the three review screens' components and state, keyboard shortcuts, optimistic-update logic.
- **Accept:** a night's shortlist can be triaged by keyboard only; tests cover approve, reject and rollback.

### M10. Recon and Scribe
- **Deliver:** Recon per §8, Scribe per §8, `lint_draft`, contact deletion on close.
- **Paul writes:** source-URL grounding rule, `lint_draft`, few-shot examples.
- **Accept:** every stored contact has a reachable `source_url`; test shows linter rejecting an invented claim.

### M11. Shipping
- **Deliver:** OpenTelemetry tracing across web → workflow → agents → Claude (no keys in spans); VPS deployment with Compose and HTTPS, prod key in a `chmod 600` env file, CI secrets via GitHub Actions secrets; backups with a restore script; README with diagram, demo GIF, depth scorecard.
- **Paul writes:** README "what I'd do differently", deployment steps, backup restore test.
- **Accept:** a week of unattended nightly runs; any run traceable by ID; a backup restored once.

---

## 12. Definition of done (every task)

- Code builds, lint passes, tests pass locally and in CI.
- New behavior has tests; no network calls in tests.
- No hard rule in §2 broken.
- Config and `.env.example` updated if new settings were added.
- `docs/TODO.md` updated with anything deferred.
