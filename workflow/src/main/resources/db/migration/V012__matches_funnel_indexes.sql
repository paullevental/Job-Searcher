-- Stage 2 reads only selected rows; Gate 1 reads the top of the ranking.
CREATE INDEX ix_matches_selected ON matches (posting_id) WHERE selected_for_analysis;

CREATE INDEX ix_matches_similarity ON matches (similarity DESC);
