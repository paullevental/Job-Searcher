CREATE TABLE resume_chunks (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    version text NOT NULL,
    kind text NOT NULL CONSTRAINT ck_resume_chunks_kind CHECK (kind IN ('bullet', 'project', 'course')),
    ordinal integer NOT NULL CONSTRAINT ck_resume_chunks_ordinal CHECK (ordinal >= 0),
    text text NOT NULL,
    embedding vector(768),
    embedding_model text,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_resume_chunks_version_ordinal UNIQUE (version, ordinal),
    CONSTRAINT ck_resume_chunks_embedding_model CHECK (embedding IS NULL OR embedding_model IS NOT NULL)
);
