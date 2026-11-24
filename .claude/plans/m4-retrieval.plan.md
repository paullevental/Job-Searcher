# Plan: M4 Retrieval

**Source:** AGENTS.md §11 M4, §8 Matcher · `docs/OFFERRADAR.md` §M4 · `docs/TODO.md` M4 block
**Approved:** 2026-11-22
**Complexity:** Large

No migration needed. V004 `resume_chunks`, V005 `matches` and V008's two HNSW
indexes already exist; §2 rule 6 makes them unchangeable anyway.

## Decisions (settled 2026-11-22)

| # | Decision | Chosen |
|---|---|---|
| D1 | Embedding prefix | `search_document:` on **both** sides — resume chunks and postings are both documents, so the task is symmetric |
| D2 | Posting embedding text | Uniform `title + company + location + term`. Description ignored: every GitHub-list posting has `description IS NULL`, so using it would create two populations with different similarity scales |
| D3 | `resume_version` | `sha256(resume.md)[:12]` — content-addressed, so an edit forces a re-embed with no manual bump |
| D4 | Rating scale, metric, sampling | Paul's, in P5. Must be a blind sample from ranks 21–200 with a holdout (see Risks) |
| D5 | Dedup cosine source | Embed `normalized_title` on the fly. `dedup_pairs.json` holds titles only, so a whole-posting vector would not transfer; no migration, no second column |

## Scope deviation

M4's acceptance check names "an ADR with numbers comparing RAG to whole-resume".
That needs an LLM call, and §2 rule 8 routes every LLM call through the shared
client, which is M5. **The RAG-vs-whole-resume experiment moves to M5.** ADR-005
covers the embedding-model comparison, the ranking formula and the re-derived
dedup thresholds.

## Phases

| P | Scope | Owner |
|---|---|---|
| P1 | Ollama in Compose; config + `.env.example`; `pgvector` dep; `Vector(768)` replacing `NullType`; asyncpg codec; `resume_chunks`/`matches` declared; `EmbeddingProvider` + Ollama client + fake | agent |
| P2 | `chunk_resume()` | **Paul** — storage, CLI and contract tests are the agent's |
| P3 | Posting embedding on first sight: separate pass after `run_scout`, own transaction, `WHERE embedding IS NULL OR embedding_model <> :model` | agent |
| P4 | `rank_postings(k)`, `retrieve_resume_context(text, k)`, ranking formula | **Paul** — pgvector query helpers, `matches` upsert and contract tests are the agent's |
| P5 | Ranking eval, retrieval eval, embedding-model comparison | **Paul** — harness scaffold mirroring `dedup/eval.py` is the agent's |
| P6 | Fuzzy dedup swapped to cosine; thresholds re-derived on the 34 labelled pairs | agent |
| P7 | ADR-005 prose | **Paul** — empty file, `docs/milestones/M4.md`, TODO updates and the `resume.md` † flip are the agent's |

Contract tests for Paul's items use `xfail(raises=NotImplementedError, strict=True)`,
the M2 phase-6 pattern: green on a correct implementation, loud on a wrong one.

## Design notes

- **HNSW serves retrieval, not ranking.** The index only accelerates `ORDER BY`
  against a single query vector, so it fits `retrieve_resume_context`. An
  aggregate-over-chunks ranking formula is an exact join either way: ~40 chunks
  x ~4k open postings is ~160k dot products, which Postgres does not notice.
  Do not contort the formula to court the index.
- **`bge-small` is 384-dim and the column is `vector(768)`**, so the model
  comparison lives entirely in a script. It cannot be stored.
- **Evals cannot call Ollama in CI** (rule 5). Fixtures record vectors and CI
  scores the formula over them; real-model runs are a local `make` target whose
  numbers land in ADR-005.
- **"No paid API calls in stage 1" already has a guard.** The new `embeddings/`
  package sits outside `llm/`, so `scripts/check_llm_chokepoint.py` fails the
  build if a model SDK is imported into it.
- Greenhouse `content` is escaped HTML. D2 makes this moot for ranking, but any
  future use of the description needs an HTML-to-text step first.

## Risks

| Risk | L | Mitigation |
|---|---|---|
| Eval leakage: Paul writes the resume, the formula and the ratings | High | Holdout never used while tuning; blind-rate a sample from ranks 21-200 |
| M4's stated acceptance check cannot detect the risk it exists for — recall on a funnel-filtered sample trends to 100% | High | Same fix; recorded as `[blocking]` in `docs/TODO.md` |
| Ollama beside the 5-service stack on an 8 GB machine | Med | Model pulled once into a named volume; Docker down the moment a container run finishes |
| Threshold re-derivation regresses dedup precision | Med | The existing precision-1.0 floor test fails the build |
| Stage-1 recall is a hard ceiling on the whole system | Med | Both ADR-004 guards are provenance rules, not thresholds, so they survive the swap |

## Acceptance (§11 M4)

- [ ] Ollama in Compose; `EmbeddingProvider` with no paid path
- [ ] Resume chunked and embedded once per `resume_version`
- [ ] Every posting embedded once on first sight
- [ ] `rank_postings(k)` writes `matches.similarity`
- [ ] `retrieve_resume_context(text, k)`
- [ ] Ranking recall and retrieval recall@k reported
- [ ] ADR-005 with comparison numbers
- [ ] Fuzzy dedup on embeddings, thresholds re-derived, precision floor held
- [ ] No paid API call anywhere in stage 1
