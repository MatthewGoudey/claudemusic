-- Show interest tracking.
-- Tracks user intent for individual shows: going, interested, not_interested, cant_afford.

CREATE TABLE IF NOT EXISTS show_interests (
    interest_id  SERIAL PRIMARY KEY,
    show_id      INTEGER NOT NULL REFERENCES chicago_shows(show_id) ON DELETE CASCADE,
    status       TEXT NOT NULL CHECK (status IN (
                     'going', 'interested', 'not_interested', 'cant_afford'
                 )),
    note         TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(show_id)
);

CREATE INDEX IF NOT EXISTS idx_show_interests_status ON show_interests (status);
