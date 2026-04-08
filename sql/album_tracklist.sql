-- Migration: Add album_tracklist table
-- Stores canonical tracklist sizes for album completion math.
-- Populated by the MusicBrainz resolver.

CREATE TABLE IF NOT EXISTS album_tracklist (
    raw_artist  TEXT NOT NULL,
    raw_album   TEXT NOT NULL,
    track_count INTEGER NOT NULL,
    source      TEXT NOT NULL DEFAULT 'musicbrainz',
    resolved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (raw_artist, raw_album)
);

-- Index for case-insensitive lookups
CREATE INDEX IF NOT EXISTS idx_album_tracklist_lower
    ON album_tracklist (LOWER(raw_artist), LOWER(raw_album));

-- Read-only role for /api/query endpoint security
-- Run these as a superuser/owner:
--
-- CREATE ROLE api_readonly WITH LOGIN PASSWORD 'your_password';
-- GRANT CONNECT ON DATABASE your_db TO api_readonly;
-- GRANT USAGE ON SCHEMA public TO api_readonly;
-- GRANT SELECT ON ALL TABLES IN SCHEMA public TO api_readonly;
-- ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO api_readonly;
--
-- Then set DATABASE_URL_READONLY in .env to use this role's connection string.
