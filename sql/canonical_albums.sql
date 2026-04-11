CREATE TABLE IF NOT EXISTS canonical_albums (
    id SERIAL PRIMARY KEY,
    artist TEXT NOT NULL,
    album TEXT NOT NULL,
    year INTEGER,
    genre TEXT NOT NULL,
    subgenre TEXT,
    tier TEXT NOT NULL DEFAULT 'essential',
    description TEXT,
    added_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(artist, album)
);

CREATE INDEX IF NOT EXISTS idx_canonical_genre ON canonical_albums(genre);
CREATE INDEX IF NOT EXISTS idx_canonical_genre_tier ON canonical_albums(genre, tier);
