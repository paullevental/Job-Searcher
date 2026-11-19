CREATE TABLE contacts (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    posting_id bigint NOT NULL REFERENCES postings (id) ON DELETE RESTRICT,
    name text NOT NULL,
    title text,
    source_url text NOT NULL,
    confidence real CONSTRAINT ck_contacts_confidence CHECK (confidence BETWEEN 0 AND 1),
    -- Soft delete: outreach_drafts references contacts, and a sent_manually
    -- draft is the only human-authorised record in the system.
    deleted_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TRIGGER trg_contacts_set_updated_at
    BEFORE UPDATE ON contacts
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();

CREATE TABLE outreach_drafts (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    contact_id bigint NOT NULL REFERENCES contacts (id) ON DELETE RESTRICT,
    body text NOT NULL,
    edited_body text,
    status text NOT NULL DEFAULT 'draft' CONSTRAINT ck_outreach_drafts_status CHECK (status IN ('draft', 'approved', 'sent_manually', 'discarded')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TRIGGER trg_outreach_drafts_set_updated_at
    BEFORE UPDATE ON outreach_drafts
    FOR EACH ROW
    EXECUTE FUNCTION set_updated_at();
