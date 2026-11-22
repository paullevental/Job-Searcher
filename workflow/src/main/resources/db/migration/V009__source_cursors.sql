-- Polling cursor for the GitHub README lists. ATS boards keep theirs in
-- ats_boards: that table is company-scoped and its provider CHECK admits only
-- the four ATS vendors, so a repo cannot be represented there.
CREATE TABLE source_cursors (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source text NOT NULL CONSTRAINT ck_source_cursors_source CHECK (source IN ('simplify', 'ouckah')),
    -- owner/repo: SimplifyJobs publishes Summer and Off-Season separately.
    cursor_key text NOT NULL,
    -- The ETag is the whole cursor: GitHub derives it from content, so an
    -- unchanged README answers 304 and is never parsed again.
    etag text,
    last_polled_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_source_cursors_source_key UNIQUE (source, cursor_key)
);

CREATE TRIGGER trg_source_cursors_set_updated_at
    BEFORE UPDATE ON source_cursors
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();
