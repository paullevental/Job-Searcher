-- Staleness query: open postings not seen in over 21 days.

EXPLAIN (ANALYZE, BUFFERS)
SELECT id, company_id, last_seen_at
FROM postings
WHERE status = 'open' AND last_seen_at < now() - interval '21 days';

-- Forcing the planner off the index is how you get a before/after on
-- identical data, rather than comparing two different databases.
-- SET LOCAL confines the change to this transaction.
BEGIN;

SET LOCAL enable_indexscan = off;
SET LOCAL enable_bitmapscan = off;

EXPLAIN (ANALYZE, BUFFERS)
SELECT id, company_id, last_seen_at
FROM postings
WHERE status = 'open' AND last_seen_at < now() - interval '21 days';

COMMIT;
