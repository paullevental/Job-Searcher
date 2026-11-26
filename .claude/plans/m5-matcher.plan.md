# Plan: M5 Matcher

**Source:** AGENTS.md §11 M5, §8 Matcher / Providers / LLM client, §9 · `docs/OFFERRADAR.md` §M5 · `docs/TODO.md` M5 block · `docs/milestones/M4.md` (RAG experiment moved here)
**Status:** built 2026-11-24; state in `docs/milestones/M5.md`
**Complexity:** Large

## Build rule: one task, one job

Applies to every task below, agent or Paul.

- Write code that does exactly the task's stated job. Nothing else.
- No speculative options, flags, hooks, base classes or "for later" parameters.
- No generalising existing code to fit a new caller. Reuse as-is or write new.
- No drive-by refactors, renames or fixes in files the task doesn't own. Found a problem → `docs/TODO.md`.
- Each task lists **Does** and **Does not**. Reviewer rejects anything outside **Does**.
- Smallest diff that passes the task's **Validate**. If a task needs >~100 lines, stop and show Paul first (§3).

## Decisions (Paul settles before P1)

| # | Question | Recommendation | Why |
|---|---|---|---|
| D1 | Memo skip key: add `model`, `provider`? | **Yes**, full 5-tuple `(description_hash, prompt_version, resume_version, model, provider)` | Without them the second provider's eval run is skipped as cached → acceptance check (both providers) impossible. Matches V005 unique key |
| D2 | Batches API | **Drop for M5**, real-time calls only; ADR-006 records it; amend §5 | Async ≤24h, no `awaiting_batch` state in §6, ~20 calls/night makes discount immaterial vs $0.50 cap. Adding it = new state + poller = most bug surface in milestone |
| D3 | Resume context: RAG or whole resume | **Whole resume** in production prompt; RAG only as eval variant | ~600 tokens; fixed prefix is what prompt caching needs; per-posting chunks break cache |
| D4 | Budget check shape | **Reservation**: atomic `UPDATE agent_runs SET spent_usd = spent_usd + :reserved WHERE spent_usd + :reserved <= budget_usd`, reconcile after response | Point check lets one call overshoot cap. Paul's code (budget guards) |
| D5 | Eval rating scale | Paul's 1–5, per OFFERRADAR §M5; agreement metric Paul's | Paul writes eval script |
| D6 | Ollama scoring model | Paul picks (e.g. `qwen2.5:7b-instruct`, must support tools); 8 GB RAM limit | Needs tool calling for same schema path |

## Owner split

**Paul writes** (§11 M5): prefilter rules, `LLMProvider` interface, scoring prompt + JSON schema, cost calculation + budget guards, eval script.
**Agent writes**: everything else below. For Paul's items the agent writes only stubs (`NotImplementedError`) + strict-xfail contract tests, M4 pattern, **only if Paul asks**.

## Phases

| P | Task | Owner | Depends |
|---|---|---|---|
| P0 | Decisions D1–D6; AGENTS.md line 5 → M5 | Paul | — |
| P1 | Contracts: config + `.env.example`, table decls, `anthropic` dep | agent | P0 |
| P1b | V012 `matches` indexes | **Paul** (migration SQL) | P0 |
| P2 | `LLMProvider` interface | **Paul** | P1 |
| P3 | Anthropic provider | agent | P2 |
| P4 | Ollama provider | agent | P2 |
| P5 | Shared client: retry + timeout + `llm_calls` log + call counter | agent | P2, P6 |
| P6 | Cost calc + budget reservation | **Paul** | P1 |
| P7 | Log/exception redaction at `llm/` boundary | agent | P1 |
| P8 | Prefilter | **Paul** | P1 |
| P9 | Gate 1 selection + `AUTO_FORWARD_TOP_N` | agent | P1 |
| P10 | Scoring prompt `score_v1.md` + tool schema | **Paul** | P2 |
| P11 | `score_selected`: stage-2 runner + memoization | agent | P3–P6, P9, P10 |
| P12 | Eval fixture (30 postings) scaffold + rating sampler | agent | P1 |
| P13 | Eval script; `score_v2.md` | **Paul** | P11, P12 |
| P14 | Cost-per-night report: funnel vs all | agent | P11 |
| P15 | Guards: chokepoint scope, no send-capable tools | agent | P3, P4 |
| P16 | Whole-feature review, `docs/milestones/M5.md`, TODO | agent (Opus) | all |

## Tasks

### P1 — contracts
- **Does:** add §9 vars to `config.py` + `.env.example` (`RUN_BUDGET_USD`, `MAX_LLM_CALLS_PER_RUN`, `LLM_MAX_TOKENS_SCORE`, `LLM_TIMEOUT_SECONDS`, `LLM_MAX_RETRIES`, `GATE1_SHORTLIST_SIZE`, `AUTO_FORWARD_TOP_N`, `LLM_PROVIDER`, `LLM_SCORE_MODEL`, `OLLAMA_LLM_MODEL`, `ANTHROPIC_API_KEY` as `SecretStr`, per-model token prices); declare `match_analyses`, `agent_runs`, `llm_calls` in `db/tables.py`; add `anthropic` to `pyproject.toml`; pass new vars to `agents` in Compose.
- **Does not:** add vars no M5 task reads; add Recon/Scribe token-limit vars; touch existing settings.
- **Mirror:** `config.py` Field+alias pattern; `_embedding_provider_is_known` validator for `LLM_PROVIDER`.
- **Validate:** `make lint` (incl. `check_env_example.py`); `test_db_tables.py` extended.

### P1b — V012 `matches` indexes (Paul's SQL)
- **Does:** partial index `WHERE selected_for_analysis`, index `similarity DESC`. Nothing else.
- **Validate:** `make test-workflow`.

### P3 — Anthropic provider
- **Does:** implement Paul's `LLMProvider` for Anthropic: one `messages.create` with `max_tokens`, `timeout`, forced `tool_choice`, `cache_control` on system/resume block; return tool input + usage (incl. `cache_read_input_tokens`).
- **Does not:** retry, log, check budget, compute cost (P5/P6 own those); batch; stream; pick model (config does).
- **Validate:** respx fixture test: request body has `max_tokens`, `tool_choice`, `cache_control`; usage parsed.

### P4 — Ollama provider
- **Does:** same interface via Ollama `/api/chat` with `tools`; return tool args + token counts; cost 0.
- **Does not:** share code with `embeddings/ollama.py`; retry; fall back to free-text JSON parsing (§5 bans it) — no tool call → raise.
- **Validate:** respx fixture: tool-call response parsed; no-tool-call response raises.

### P5 — shared client
- **Does:** single `complete()` entry in `llm/`: budget reserve (P6) → enforce `MAX_LLM_CALLS_PER_RUN` → provider call → retry only on 429/529/timeout/5xx, max `LLM_MAX_RETRIES`, backoff+jitter → insert `llm_calls` → reconcile spend, bump `llm_call_count`. On exhaustion set run `budget_exhausted`, raise a typed error.
- **Does not:** retry 400/401/403; know about scoring or prompts; loop over postings.
- **Mirror:** `scout/retry.py` — reuse as-is if signature fits; otherwise new function, don't edit scout's.
- **Validate (acceptance):** three tests, each proves a run stops: budget cap, call cap, retry cap. Plus 401 not retried.

### P7 — redaction
- **Does:** set `httpx`/`anthropic` loggers to WARNING; wrap provider exceptions at `llm/` boundary into an error carrying status + message only, no request object.
- **Does not:** build a general logging framework.
- **Validate:** test: raised error's `repr`/`str`/`__cause__` chain contains no `x-api-key`.

### P9 — Gate 1 selection
- **Does:** `shortlist(session, n=GATE1_SHORTLIST_SIZE)` reads top-n `matches` by similarity; `select_for_analysis(session, posting_ids)` sets flag; `auto_forward(session, n=AUTO_FORWARD_TOP_N)` selects top n, no-op at 0.
- **Does not:** write `approvals` (M7's); add UI/API endpoints (M6/M9); rank (M4's `rank_postings`).
- **Validate:** integration tests: n=0 selects nothing; n=3 selects exactly top 3.

### P11 — stage-2 runner + memoization
- **Does:** `score_selected(session, client, run_id)`: query `selected_for_analysis = true` + open; skip rows already in `match_analyses` on D1 key; build prompt from Paul's `score_vN.md` + whole resume (D3); call P5; insert `match_analyses`.
- **Does not:** read unselected postings in any query; retrieve chunks (eval only); batch; retry (P5 does).
- **Validate (acceptance):** test with 5 postings, 2 selected → provider called exactly twice, unselected ids never in any request. Second run → zero calls (memo).

### P12 — eval scaffold
- **Does:** `matcher/score_eval.py` loader for `score_eval.json` (30 postings: text, Paul's 1–5 rating); `make sample-score-eval` picks 30 blind across rank bands; recorded-response fixtures so CI scores offline.
- **Does not:** compute agreement (Paul's P13); call any model in CI.
- **Mirror:** `matcher/eval.py` loader + `split_holdout`.
- **Validate:** loader tests; `check_offline_tests.py`.

### P14 — cost report
- **Does:** script reads `llm_calls` for a run, prints actual cost and projected cost of scoring every open posting at the same mean tokens/call.
- **Does not:** optimise anything; write to DB.
- **Validate:** unit test on fixed rows.

### P15 — guards
- **Does:** `check_llm_chokepoint.py` also scans `agents/tests/`, `scripts/`, fails if package dir missing; test asserting every tool schema passed to `complete()` is in an allowlist (score tool only).
- **Does not:** refactor the three guards' shared walk (TODO item, not M5).
- **Validate:** one tripping + one clean fixture each.

### P16 — integration
- Whole-feature review (Opus), given acceptance checks not file list. Per-component reviews (Sonnet `ecc:python-reviewer`) after each of P3–P15 lands.
- `docs/milestones/M5.md`, TODO updates, ADR-006 empty file (Paul's prose).
- Docker down after container runs.

## Execution (per memory: multiple agents)

1. Sequential: P1 (one author). Paul: P0, P1b, P2, P6, P8, P10.
2. Parallel agents once P2 lands: {P3}, {P4}, {P7}, {P9}, {P12}, {P15}; {P5} once P6 lands. Each told: own files only, follow Build rule.
3. Reviewer behind each, checking **Does not** list explicitly.
4. Sequential: P11, P14, P16.

## Patterns to mirror

| Category | Source |
|---|---|
| Config | `config.py` Field/alias, validator per enum-like setting |
| Errors | `embeddings/errors.py` `EMBEDDING_ERRORS` tuple → `llm/errors.py` |
| Fakes | `embeddings/fake.py` → `llm/fake.py` deterministic provider |
| Paul stubs | `matcher/rank.py` `NotImplementedError` + `# Paul writes this:` line |
| Tests | respx fixtures; `xfail(raises=NotImplementedError, strict=True)`; integration under `tests/integration/` |
| Eval | `matcher/eval.py` loader, `split_holdout`, `format_report` |

## Validation
```bash
make lint && make test-agents-offline && make test-agents && make test-workflow
```

## Risks

| Risk | L | Mitigation |
|---|---|---|
| Real key leaks via exception/log | Med | P7 + test; no real key in tests |
| Ollama LLM + Postgres on 8 GB | High | Small model (D6); Docker down after tests |
| Ollama model emits no/invalid tool call | High | P4 raises, counted as failure in eval, never parsed as text |
| Cache silently not hit (prefix varies) | Med | P3 test asserts identical system block bytes across postings; report `cache_read_tokens` |
| Eval leakage (Paul writes prompt + ratings) | Med | Holdout untouched while iterating prompts |
| Scope creep into M6 (graph waits, gates API) | Med | Build rule; P9/P11 Does-not lists |

## Acceptance (§11 M5)
- [ ] Eval results for ≥2 prompt versions × both providers
- [ ] Cost per night: funnel vs scoring all postings (P14)
- [ ] Tests: budget cap, call cap, retry cap each stop a run (P5)
- [ ] No unselected posting reaches stage 2 (P11)
- [ ] Every task's diff stays inside its **Does** list
