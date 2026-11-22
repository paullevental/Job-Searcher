-- V003 shipped `posting_sightings.source` as unconstrained text, so a typo
-- ('simplfy', 'Simplify') inserts cleanly and fragments the column's value
-- space with no error anywhere. M2 is the first milestone to write the column,
-- and M3's staleness rules branch on it. Rule 6 forbids editing V003, so the
-- CHECK arrives here. Vendors not yet adapted are listed to avoid a migration
-- when they are.
ALTER TABLE posting_sightings
    ADD CONSTRAINT ck_posting_sightings_source
    CHECK (source IN ('simplify', 'ouckah', 'greenhouse', 'lever', 'ashby', 'workable', 'manual'));
