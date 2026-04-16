-- Unified album checklist — master dedup layer for all "albums to listen to" sources.
-- Each album appears once; sources tracked in checklist_sources junction table.

CREATE TABLE IF NOT EXISTS checklist_albums (
    id          SERIAL PRIMARY KEY,
    artist      TEXT NOT NULL,
    album       TEXT NOT NULL,
    year        INTEGER,
    added_at    TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(artist, album)
);

CREATE INDEX IF NOT EXISTS idx_checklist_artist ON checklist_albums(LOWER(artist));
