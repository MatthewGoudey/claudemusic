-- Pre-materialized matches between checklist albums and listen events.
-- Populated by POST /api/checklist/matches/refresh (two-pass normalized matching).

CREATE TABLE IF NOT EXISTS checklist_listen_matches (
    checklist_id  INTEGER NOT NULL REFERENCES checklist_albums(id) ON DELETE CASCADE,
    event_id      INTEGER NOT NULL,
    match_method  TEXT NOT NULL DEFAULT 'normalized',
    matched_at    TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (checklist_id, event_id)
);

CREATE INDEX IF NOT EXISTS idx_clm2_checklist ON checklist_listen_matches(checklist_id);
CREATE INDEX IF NOT EXISTS idx_clm2_event ON checklist_listen_matches(event_id);
