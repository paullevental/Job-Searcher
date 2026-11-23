-- §8.3's middle band had nowhere to live: a similarity between
-- DEDUP_REVIEW_THRESHOLD and DEDUP_MERGE_THRESHOLD "flags possible duplicate",
-- but ck_postings_status admits only open/stale/closed/merged and no other
-- column held the pairing, so the verdict was computed and discarded. The row
-- stays `open` and still reaches Matcher -- this is a hint for Gate 1, not a
-- status transition. Rule 6 forbids editing V003, so it arrives here.
ALTER TABLE postings
    ADD COLUMN possible_duplicate_of bigint REFERENCES postings (id) ON DELETE SET NULL;

ALTER TABLE postings
    ADD CONSTRAINT ck_postings_possible_duplicate_not_self CHECK (possible_duplicate_of <> id),
    -- A merged row's winner is merged_into_id; carrying both would be two answers
    -- to one question.
    ADD CONSTRAINT ck_postings_possible_duplicate_not_merged
        CHECK (possible_duplicate_of IS NULL OR merged_into_id IS NULL);

-- Gate 1 reads "what is this flagged against"; the partial index skips the
-- overwhelming majority of rows that carry no flag.
CREATE INDEX ix_postings_possible_duplicate_of ON postings (possible_duplicate_of)
    WHERE possible_duplicate_of IS NOT NULL;
