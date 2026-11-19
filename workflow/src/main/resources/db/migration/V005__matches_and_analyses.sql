CREATE TABLE matches (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    posting_id bigint NOT NULL UNIQUE REFERENCES postings (id) ON DELETE CASCADE,
    similarity real NOT NULL CONSTRAINT ck_matches_similarity CHECK (similarity BETWEEN -1 AND 1),
    embedding_model text NOT NULL,
    resume_version text NOT NULL,
    selected_for_analysis boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TRIGGER trg_matches_set_updated_at
    BEFORE UPDATE ON matches
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();

CREATE TABLE match_analyses (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    posting_id bigint NOT NULL REFERENCES postings (id) ON DELETE CASCADE,
    -- Stored here, not read live from postings: re-polling changes the
    -- posting's current hash and would silently defeat the memoization skip.
    description_hash text NOT NULL,
    prompt_version text NOT NULL,
    resume_version text NOT NULL,
    model text NOT NULL,
    provider text NOT NULL CONSTRAINT ck_match_analyses_provider CHECK (provider IN ('anthropic', 'ollama')),
    score smallint NOT NULL CONSTRAINT ck_match_analyses_score CHECK (score BETWEEN 0 AND 100),
    matched_skills jsonb NOT NULL DEFAULT '[]'::jsonb,
    missing_skills jsonb NOT NULL DEFAULT '[]'::jsonb,
    why_fit text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_match_analyses_scoring_inputs UNIQUE (posting_id, description_hash, prompt_version, resume_version, model, provider),
    CONSTRAINT ck_match_analyses_matched_skills_array CHECK (jsonb_typeof(matched_skills) = 'array'),
    CONSTRAINT ck_match_analyses_missing_skills_array CHECK (jsonb_typeof(missing_skills) = 'array')
);
