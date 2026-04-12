CREATE TABLE IF NOT EXISTS canonical_listen_matches (
    canonical_id  INTEGER NOT NULL REFERENCES canonical_albums(id) ON DELETE CASCADE,
    event_id      INTEGER NOT NULL,
    match_method  TEXT NOT NULL DEFAULT 'normalized',
    matched_at    TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (canonical_id, event_id)
);

CREATE INDEX IF NOT EXISTS idx_clm_canonical ON canonical_listen_matches(canonical_id);
CREATE INDEX IF NOT EXISTS idx_clm_event ON canonical_listen_matches(event_id);
