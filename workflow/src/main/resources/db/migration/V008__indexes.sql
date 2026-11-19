CREATE INDEX ix_postings_company_id ON postings (company_id);

CREATE INDEX ix_postings_merged_into_id ON postings (merged_into_id);

-- Staleness sweep: last_seen_at > 21 days, open rows only.
CREATE INDEX ix_postings_last_seen_at ON postings (last_seen_at) WHERE status = 'open';

CREATE INDEX ix_outreach_drafts_contact_id ON outreach_drafts (contact_id);

CREATE INDEX ix_llm_calls_run_id ON llm_calls (run_id);

CREATE INDEX ix_contacts_posting_id ON contacts (posting_id) WHERE deleted_at IS NULL;

-- Poll queue.
CREATE INDEX ix_ats_boards_last_polled_at ON ats_boards (last_polled_at);

-- 5-minute reconciler scans for awaiting_*.
CREATE INDEX ix_agent_runs_status ON agent_runs (status);

-- vector_cosine_ops, not l2: stage 1 ranks by cosine similarity, and an l2
-- opclass would leave the index unused rather than fail.
CREATE INDEX ix_postings_embedding ON postings USING hnsw (embedding vector_cosine_ops);

CREATE INDEX ix_resume_chunks_embedding ON resume_chunks USING hnsw (embedding vector_cosine_ops);
