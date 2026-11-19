CREATE EXTENSION IF NOT EXISTS vector;

-- Shared by both services (agents + workflow) so "touch updated_at" cannot
-- be forgotten by either language; enforced here, not in application code.
CREATE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
