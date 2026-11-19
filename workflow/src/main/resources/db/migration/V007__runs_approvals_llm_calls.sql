CREATE TABLE agent_runs (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    status text NOT NULL DEFAULT 'running' CONSTRAINT ck_agent_runs_status CHECK (status IN ('running', 'awaiting_gate_1', 'awaiting_gate_2', 'awaiting_gate_3', 'done', 'failed', 'budget_exhausted')),
    budget_usd numeric(10,4) NOT NULL CONSTRAINT ck_agent_runs_budget_usd CHECK (budget_usd >= 0),
    -- numeric, never float: spent_usd accumulates by repeated addition
    -- straight into a hard spend cap.
    spent_usd numeric(12,6) NOT NULL DEFAULT 0 CONSTRAINT ck_agent_runs_spent_usd CHECK (spent_usd >= 0),
    llm_call_count integer NOT NULL DEFAULT 0 CONSTRAINT ck_agent_runs_llm_call_count CHECK (llm_call_count >= 0),
    langgraph_thread_id text,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_agent_runs_finished_after_started CHECK (finished_at IS NULL OR finished_at >= started_at),
    CONSTRAINT uq_agent_runs_langgraph_thread_id UNIQUE (langgraph_thread_id)
);

CREATE TRIGGER trg_agent_runs_set_updated_at
    BEFORE UPDATE ON agent_runs
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();

CREATE TABLE approvals (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id bigint NOT NULL REFERENCES agent_runs (id) ON DELETE CASCADE,
    gate smallint NOT NULL CONSTRAINT ck_approvals_gate CHECK (gate IN (1, 2, 3)),
    item_type text NOT NULL CONSTRAINT ck_approvals_item_type CHECK (item_type IN ('posting', 'contact', 'draft')),
    -- Polymorphic reference, deliberately no FK: the application must check
    -- the target exists.
    item_id bigint NOT NULL,
    decision text NOT NULL CONSTRAINT ck_approvals_decision CHECK (decision IN ('approved', 'rejected')),
    decided_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_approvals_run_gate_item UNIQUE (run_id, gate, item_type, item_id)
);

CREATE TABLE llm_calls (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id bigint NOT NULL REFERENCES agent_runs (id) ON DELETE CASCADE,
    agent text NOT NULL CONSTRAINT ck_llm_calls_agent CHECK (agent IN ('supervisor', 'scout', 'matcher', 'recon', 'scribe')),
    model text NOT NULL,
    input_tokens integer NOT NULL CONSTRAINT ck_llm_calls_input_tokens CHECK (input_tokens >= 0),
    output_tokens integer NOT NULL CONSTRAINT ck_llm_calls_output_tokens CHECK (output_tokens >= 0),
    cache_read_tokens integer NOT NULL DEFAULT 0 CONSTRAINT ck_llm_calls_cache_read_tokens CHECK (cache_read_tokens >= 0),
    cost_usd numeric(12,6) NOT NULL CONSTRAINT ck_llm_calls_cost_usd CHECK (cost_usd >= 0),
    latency_ms integer NOT NULL CONSTRAINT ck_llm_calls_latency_ms CHECK (latency_ms >= 0),
    prompt_version text,
    created_at timestamptz NOT NULL DEFAULT now()
);
