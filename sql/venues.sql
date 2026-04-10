-- Venue travel time data.
-- Precomputed distances from user's home address via Google Distance Matrix API.

CREATE TABLE IF NOT EXISTS venues (
    venue_id           SERIAL PRIMARY KEY,
    venue_name         TEXT NOT NULL UNIQUE,
    travel_driving_min SMALLINT,
    travel_transit_min SMALLINT,
    travel_walking_min SMALLINT,
    travel_best_min    SMALLINT GENERATED ALWAYS AS (
        LEAST(
            COALESCE(travel_driving_min, 999),
            COALESCE(travel_transit_min, 999),
            COALESCE(travel_walking_min, 999)
        )
    ) STORED,
    geocode_source     TEXT,
    travel_computed_at TIMESTAMPTZ,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_venues_name ON venues (LOWER(venue_name));
