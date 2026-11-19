CREATE TABLE companies (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name text NOT NULL,
    normalized_name text NOT NULL UNIQUE,
    domain text,
    source text NOT NULL CONSTRAINT ck_companies_source CHECK (source IN ('yc', 'manual', 'github_list', 'ats_discovery')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TRIGGER trg_companies_set_updated_at
    BEFORE UPDATE ON companies
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();

CREATE TABLE ats_boards (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id bigint NOT NULL REFERENCES companies (id) ON DELETE CASCADE,
    provider text NOT NULL CONSTRAINT ck_ats_boards_provider CHECK (provider IN ('greenhouse', 'lever', 'ashby', 'workable')),
    board_token text NOT NULL,
    last_polled_at timestamptz,
    etag text,
    consecutive_misses integer NOT NULL DEFAULT 0 CONSTRAINT ck_ats_boards_consecutive_misses CHECK (consecutive_misses >= 0),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_ats_boards_company_provider_token UNIQUE (company_id, provider, board_token)
);

CREATE TRIGGER trg_ats_boards_set_updated_at
    BEFORE UPDATE ON ats_boards
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();
