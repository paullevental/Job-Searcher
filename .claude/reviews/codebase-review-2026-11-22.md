# Codebase Review — 2026-11-22

**Scope**: whole codebase (working tree clean, no diff to review)
**Decision**: REQUEST CHANGES — 2 HIGH, 3 MEDIUM, 3 LOW
**Validation**: all 4 suites green

| Check | Result |
|---|---|
| ruff | Pass |
| mypy --strict (34 files) | Pass |
| pytest (agents, 195 incl. integration) | Pass |
| gradle test (workflow) | Pass |
| tsc --noEmit / eslint / vitest (web) | Pass |

Every finding below was reproduced. None is caught by the existing suite.

---

## HIGH

### H1 — Miss sweep is scoped by `source`, not by the cursor that was polled

`agents/src/offerradar_agents/dedup/staleness.py:56-63` (`_polled_by`), reached from
`scout/runner.py:164-169`.

`ListCursor.company_id` is `None`, so the candidate set is *every open posting with a
`simplify` sighting* — across both repos. The shipped default is two repos
(`config.py:51-54`), and they publish disjoint postings.

Two repos alone is survivable: each repo's own poll resets `missed_polls` to 0, so the
counter oscillates 0↔1 and never reaches the limit. **A 304 breaks the reset** — and a
304 is the normal steady state for an unchanged README, which is the entire point of the
ETag cursor.

Sequence (reproduced):

```
seeded:      newgrad=open missed=0   summer=open missed=0
run 1:  Summer fresh, New-Grad 304 -> newgrad=open missed=1
run 2:  Summer fresh, New-Grad 304 -> newgrad=stale missed=2   <-- retired while live
re-list New-Grad with the same posting -> newgrad=stale missed=2   <-- never comes back
```

The posting was never absent from its own repo. Nothing observed it gone.

The `CursorStore.company_id` docstring (`runner.py:47-50`) identifies exactly this hazard
for ATS boards and fixes it with company scoping — the GitHub-list path never got the
equivalent. Scoping must be per *cursor* (repo), not per source.

Fix needs a discriminator tying a sighting to the repo that produced it: either a
`cursor_key` column on `posting_sightings`, or a scope predicate on `CursorStore`
alongside `company_id`.

### H2 — `upsert_posting` never resets `status` or `missed_polls`

`agents/src/offerradar_agents/scout/repository.py:73-90`. The `ON CONFLICT ... set_`
updates title, location, term, url, description and `last_seen_at`, but not `status` or
`missed_polls`.

A retired posting that is re-listed stays retired permanently — invisible to Matcher,
and `load_open_posting` returns `None` so dedup skips it too. Reproduced:

```
first seen:       ('open',   0)
after closing:    ('closed', 2)
after re-listing: ('closed', 2)   <-- expected ('open', 0)
```

This is a bug on its own (a company reposting a req is routine) and it removes the only
recovery path from H1.

**Careful with the fix**: a blind `status='open'` violates
`ck_postings_merged_pairing CHECK ((status = 'merged') = (merged_into_id IS NOT NULL))`
and aborts the whole Scout transaction. Reset conditionally — a `CASE` that moves only
`stale`/`closed` back to `open` and leaves `merged` alone.

---

## MEDIUM

### M1 — Continuation rows inherit the wrong company after a skipped row

`agents/src/offerradar_agents/scout/readme_table.py:96-113`. `last_company` is assigned
*after* the `continue` guard, so a row filtered for a missing url or empty role never
updates it. The following `↳` rows then attach to the previous company. Reproduced:

```
company='OldCo'  role='SWE Intern'  url=https://oldco.com/a
company='OldCo'  role='ML Intern'   url=https://newco.com/b   <-- should be NewCo
```

Postings get written against the wrong `company_id`, which also mis-scopes
`open_siblings` (company-scoped) and `get_or_create_company`.

Move `last_company = company` above the guard, so a named company cell registers whether
or not its own row survives filtering.

### M2 — `parse_term` keeps source casing, so one requisition yields several dedup_keys

`agents/src/offerradar_agents/scout/normalize.py:92-99`. Company and title are casefolded
before hashing; `term` is not. The function's own comment promises `"{Season} {YYYY}"`.
Reproduced, same company and same req:

```
'... (Summer 2027)'  -> term='Summer 2027'  dedup_key=e5e9b799...
'... - SUMMER 2027'  -> term='SUMMER 2027'  dedup_key=846ff5ed...
'..., summer 2027'   -> term='summer 2027'  dedup_key=93ee7d23...
```

Three posting rows for one job — precisely what the dedup key exists to prevent. The
fuzzy fallback will not collapse them either: `open_siblings` refuses pairs sharing an
ATS board, and identical titles from one board are the common case.

Casefold the season (and store a canonical form) before it reaches `dedup_key`.

### M3 — `"ref"` in `TRACKING_PARAM_PREFIXES` strips identity params

`agents/src/offerradar_agents/scout/normalize.py:36`. The prefix match removes any param
beginning `ref`. Reproduced:

```
?reference_id=R-9981&utm_source=x  -> (both dropped)
?refresh_token_id=7                -> (dropped)
```

If a board carries the req id in a `ref*` param the `canonical_url` stops resolving.
`utm_` and `gh_src` are safe as prefixes; `ref` should be an exact match.

---

## LOW

### L1 — `_employer_url` parses the host by string-splitting

`agents/src/offerradar_agents/scout/readme_table.py:83-88`. `href.split("//")...split("/")`
returns `simplify.jobs:443`, which is not in `AGGREGATOR_HOSTS`, so an aggregator-only
(dead) row is reported live:

```
['https://simplify.jobs:443/p/x'] -> locked=False   # expected True
['/company/acme/job/1']           -> taken as the employer link, canonical_url='/apply/123'
```

Use `urlsplit(href).hostname` and skip non-http(s) schemes.

### L2 — Counterpart chosen by raw score, then classified

`agents/src/offerradar_agents/dedup/candidates.py:139-146`. A sibling scoring 0.93 with
unmatched tokens (demoted to `review`) outranks a clean 0.92 sibling that would `merge`,
so a true duplicate is only flagged. `fuzzy_match` also runs twice on the winner, emitting
the verdict log line twice. Rank by (verdict, score), not score alone.

### L3 — `_shares_an_ats_board` matches on `source`, not board token

`agents/src/offerradar_agents/dedup/candidates.py:78-90`, and the ATS side of H1 at
`staleness.py:56-63`. Both treat "same provider + same company" as "same board". A company
with two Greenhouse board tokens defeats the guard and the company-scoped miss sweep. Rare;
noting it because the board token is already in `ats_boards`.

---

## Clean

- **Security**: no hardcoded credentials; `.env` untracked; gitleaks configured; all SQL
  via SQLAlchemy Core (no string interpolation); `SecretStr` for the GitHub token;
  `_safe_target` keeps query strings out of logs.
- **Rule 2/3 enforcement**: blocklist + robots fail closed (`_DISALLOW_ALL` on 5xx, 429,
  unreachable, redirect loop); `follow_redirects=False` with a hand-rolled walk;
  `Authorization`/`Cookie` correctly stripped on cross-origin hops (`http.py:256-263`);
  refusals excluded from retry (`http.py:199-201`).
- Length-prefixed `dedup_key` hashing avoids delimiter collisions.
- `apply_poll_result` refuses an empty seen-set (`notin_([])` is SQL-true) — the right
  instinct; H1 is the same class of bug one level up.
