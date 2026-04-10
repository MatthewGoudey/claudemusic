-- Chicago Shows Pipeline Schema
-- Stores upcoming concerts from Ticketmaster, do312, and SeatGeek.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS chicago_shows (
    show_id         SERIAL PRIMARY KEY,
    artist_name     TEXT NOT NULL,
    venue_name      TEXT NOT NULL,
    show_date       DATE NOT NULL,
    show_time       TIME,
    doors_time      TIME,
    support_acts    TEXT[],
    ticket_url      TEXT,
    ticket_price    TEXT,
    age_restriction TEXT,
    sources         TEXT[] NOT NULL DEFAULT '{}',
    ticketmaster_id TEXT,
    do312_url       TEXT,
    seatgeek_id     TEXT,
    presale_name    TEXT,
    presale_start   TIMESTAMPTZ,
    presale_end     TIMESTAMPTZ,
    onsale_date     TIMESTAMPTZ,
    status          TEXT NOT NULL DEFAULT 'upcoming'
                    CHECK (status IN ('upcoming', 'sold_out', 'cancelled', 'past')),
    first_seen      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_verified   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_chicago_shows_dedup
    ON chicago_shows (LOWER(artist_name), LOWER(venue_name), show_date);
CREATE INDEX IF NOT EXISTS idx_chicago_shows_date ON chicago_shows (show_date);
CREATE INDEX IF NOT EXISTS idx_chicago_shows_artist ON chicago_shows (LOWER(artist_name));
CREATE INDEX IF NOT EXISTS idx_chicago_shows_venue ON chicago_shows (LOWER(venue_name));
CREATE INDEX IF NOT EXISTS idx_chicago_shows_first_seen ON chicago_shows (first_seen);
CREATE INDEX IF NOT EXISTS idx_chicago_shows_presale ON chicago_shows (presale_start)
    WHERE presale_start IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_chicago_shows_artist_trgm ON chicago_shows
    USING gin (LOWER(artist_name) gin_trgm_ops);

CREATE OR REPLACE FUNCTION normalize_artist(name TEXT) RETURNS TEXT AS $$
    SELECT TRIM(REGEXP_REPLACE(
        REGEXP_REPLACE(
            REGEXP_REPLACE(LOWER(name), '^\s*the\s+', '', ''),
            '\s*(feat\.?|ft\.?|with|&|and)\s+.*$', '', ''
        ), '[^a-z0-9\s]', '', 'g'
    ))
$$ LANGUAGE SQL IMMUTABLE;
