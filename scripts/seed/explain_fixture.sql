-- Volume fixture for the staleness-index demonstration (AGENTS.md §11 M1).
-- dev_seed.sql only has 6-8 companies, so Postgres sequential-scans whatever
-- you ask it; this generates ~50k postings so EXPLAIN ANALYZE in
-- explain_queries.sql can actually show the partial index replacing a
-- sequential scan.

-- An empty companies table makes `gs % cc.n` either throw (hash join) or
-- silently insert zero rows (nested loop short-circuits on the empty side);
-- the second case would let `make explain` look like it worked.
DO $$
BEGIN
    IF (SELECT count(*) FROM companies) = 0 THEN
        RAISE EXCEPTION 'companies is empty; run scripts/seed/dev_seed.sql (make seed) first';
    END IF;
END $$;

WITH seeded_companies AS (
    SELECT id, row_number() OVER (ORDER BY id) - 1 AS rn
    FROM companies
),
company_count AS (
    SELECT count(*) AS n FROM seeded_companies
)
INSERT INTO postings (
    company_id, title, normalized_title, canonical_url, dedup_key,
    status, first_seen_at, last_seen_at
)
SELECT
    sc.id,
    'Software Engineering Intern',
    'software engineering intern',
    'https://example.com/jobs/fixture-' || gs,
    'fixture-' || gs,
    CASE
        WHEN gs % 20 = 0 THEN 'closed'
        WHEN gs % 20 = 10 THEN 'stale'
        ELSE 'open'
    END,
    age.seen_at,
    age.seen_at
FROM generate_series(1, 50000) AS gs
CROSS JOIN company_count cc
JOIN seeded_companies sc ON sc.rn = gs % cc.n
-- Moduli 23 and 20 are coprime, so this age bucket moves independently of
-- the status bucket above: most rows are recent, a small tail is old
-- regardless of status, matching the 21-day staleness predicate's
-- real-world selectivity.
CROSS JOIN LATERAL (
    SELECT CASE
        WHEN gs % 23 = 0 THEN now() - ((22 + (gs % 99)) * interval '1 day')
        ELSE now() - ((gs % 21) * interval '1 day')
    END AS seen_at
) AS age
ON CONFLICT (dedup_key) DO NOTHING;

-- Fresh statistics, or the planner may stick with its old row-count
-- estimate and skip the index for the wrong reason.
ANALYZE postings;
