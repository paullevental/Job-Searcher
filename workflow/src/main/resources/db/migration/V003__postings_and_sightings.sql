CREATE TABLE postings (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id bigint NOT NULL REFERENCES companies (id) ON DELETE RESTRICT,
    title text NOT NULL,
    normalized_title text NOT NULL,
    location text,
    remote boolean NOT NULL DEFAULT false,
    term text,
    canonical_url text NOT NULL,
    dedup_key text NOT NULL UNIQUE,
    description text,
    description_hash text,
    embedding vector(768),
    embedding_model text,
    status text NOT NULL DEFAULT 'open' CONSTRAINT ck_postings_status CHECK (status IN ('open', 'stale', 'closed', 'merged')),
    merged_into_id bigint REFERENCES postings (id) ON DELETE RESTRICT,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    missed_polls integer NOT NULL DEFAULT 0 CONSTRAINT ck_postings_missed_polls CHECK (missed_polls >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    -- A merged posting always names its winner and nothing else does.
    CONSTRAINT ck_postings_merged_pairing CHECK ((status = 'merged') = (merged_into_id IS NOT NULL)),
    CONSTRAINT ck_postings_merged_not_self CHECK (merged_into_id <> id),
    CONSTRAINT ck_postings_embedding_model CHECK (embedding IS NULL OR embedding_model IS NOT NULL),
    -- Memoization key: must not drift from the text it was computed over.
    CONSTRAINT ck_postings_description_hash_pairing CHECK ((description IS NULL) = (description_hash IS NULL))
);

CREATE TRIGGER trg_postings_set_updated_at
    BEFORE UPDATE ON postings
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();

CREATE TABLE posting_sightings (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    posting_id bigint NOT NULL REFERENCES postings (id) ON DELETE CASCADE,
    source text NOT NULL,
    source_url text NOT NULL,
    seen_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    -- One row per distinct place a posting was seen, not an event log: a
    -- repeat sighting refreshes seen_at via ON CONFLICT, making Scout idempotent.
    CONSTRAINT uq_posting_sightings_posting_source_url UNIQUE (posting_id, source, source_url)
);
