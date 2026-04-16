-- Per-source metadata for checklist albums.
-- An album on both Rolling Stone and 1001 Albums gets two rows here.

CREATE TABLE IF NOT EXISTS checklist_sources (
    checklist_id INTEGER NOT NULL REFERENCES checklist_albums(id) ON DELETE CASCADE,
    source       TEXT NOT NULL,  -- 'rolling_stone_500', '1001_albums', 'aoty', 'canonical', 'manual'
    rank         INTEGER,        -- position in list (NULL for canonical/manual)
    genre        TEXT,           -- source-specific genre string (not controlled vocabulary)
    subgenre     TEXT,
    tier         TEXT,           -- only meaningful for canonical source
    description  TEXT,           -- only meaningful for canonical source
    list_year    INTEGER,        -- year of the list edition (e.g., 2024 for AOTY 2024)
    added_at     TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (checklist_id, source)
);

CREATE INDEX IF NOT EXISTS idx_cs_source ON checklist_sources(source);
