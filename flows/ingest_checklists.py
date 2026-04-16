"""
Ingest album checklist CSVs into the checklist_albums + checklist_sources tables.

Supports: Rolling Stone 500, 1001 Albums, AOTY 2007-2024.
Idempotent — safe to re-run. Uses ON CONFLICT to upsert.

Usage:
    python -m flows.ingest_checklists [csv_dir]

    csv_dir defaults to the music-pipeline repo path.
"""

import csv
import io
import logging
import os
import sys

import asyncpg
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

DEFAULT_CSV_DIR = r"C:\Users\MattG\projects\music-pipeline\music-pipeline"


def parse_rolling_stone(csv_dir: str) -> list[dict]:
    """Parse Rolling Stone Top 500 CSV (Mac Roman encoded)."""
    path = os.path.join(csv_dir, "rollingstone500.csv")
    if not os.path.exists(path):
        logger.warning(f"Rolling Stone CSV not found: {path}")
        return []

    raw = open(path, "rb").read()
    text = raw.decode("mac_roman")
    text = text.replace("\u00a0", " ")  # non-breaking space → regular space

    reader = csv.DictReader(io.StringIO(text))
    albums = []
    for row in reader:
        albums.append({
            "artist": row["Artist"].strip(),
            "album": row["Album"].strip(),
            "year": int(row["Year"]) if row.get("Year", "").strip() else None,
            "source": "rolling_stone_500",
            "rank": int(row["Number"]) if row.get("Number", "").strip() else None,
            "genre": row.get("Genre", "").strip() or None,
            "subgenre": row.get("Subgenre", "").strip() or None,
        })

    logger.info(f"Parsed {len(albums)} albums from Rolling Stone 500")
    return albums


def parse_1001_albums(csv_dir: str) -> list[dict]:
    """Parse 1001 Albums You Must Hear CSV."""
    path = os.path.join(csv_dir, "1001 Albums Spreadsheet Parsed.csv")
    if not os.path.exists(path):
        logger.warning(f"1001 Albums CSV not found: {path}")
        return []

    seen_keys = set()
    albums = []
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            artist = row.get("artist", "").strip()
            title = row.get("title", "").strip()
            if not artist or not title:
                continue

            # Dedup (Sabu Martinez appears twice at rank 11)
            key = (artist.lower(), title.lower())
            if key in seen_keys:
                continue
            seen_keys.add(key)

            year_str = row.get("year", "").strip()
            albums.append({
                "artist": artist,
                "album": title,
                "year": int(year_str) if year_str else None,
                "source": "1001_albums",
                "rank": int(row["number"]) if row.get("number", "").strip() else None,
                "genre": row.get("genre", "").strip() or None,
                "subgenre": None,
            })

    logger.info(f"Parsed {len(albums)} albums from 1001 Albums")
    return albums


def parse_aoty(csv_dir: str) -> list[dict]:
    """Parse AOTY 2007-2024 CSV (Artist - Album format)."""
    path = os.path.join(csv_dir, "album_of_the_year_2007-2024 (1).csv")
    if not os.path.exists(path):
        logger.warning(f"AOTY CSV not found: {path}")
        return []

    albums = []
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, 1):
            album_field = row.get("Album", "").strip()
            year_str = row.get("Year", "").strip()

            if not album_field:
                continue

            # Split on first " - " to separate artist from album
            if " - " in album_field:
                artist, album = album_field.split(" - ", 1)
            else:
                logger.warning(f"AOTY row {i}: no ' - ' separator in '{album_field}'")
                continue

            albums.append({
                "artist": artist.strip(),
                "album": album.strip(),
                "year": None,  # AOTY year is list year, not release year
                "source": "aoty",
                "rank": i,
                "genre": None,
                "subgenre": None,
                "list_year": int(year_str) if year_str else None,
            })

    logger.info(f"Parsed {len(albums)} albums from AOTY 2007-2024")
    return albums


async def load_albums(conn: asyncpg.Connection, albums: list[dict]) -> dict:
    """Load parsed albums into checklist_albums + checklist_sources."""
    inserted = 0
    linked = 0

    for alb in albums:
        # Upsert into checklist_albums
        row = await conn.fetchrow(
            """INSERT INTO checklist_albums (artist, album, year)
               VALUES ($1, $2, $3)
               ON CONFLICT (artist, album) DO NOTHING
               RETURNING id""",
            alb["artist"], alb["album"], alb["year"],
        )

        if row:
            checklist_id = row["id"]
            inserted += 1
        else:
            # Already exists — get the id
            checklist_id = await conn.fetchval(
                "SELECT id FROM checklist_albums WHERE artist = $1 AND album = $2",
                alb["artist"], alb["album"],
            )

        if checklist_id is None:
            logger.warning(f"Could not find/insert: {alb['artist']} - {alb['album']}")
            continue

        # Upsert into checklist_sources
        await conn.execute(
            """INSERT INTO checklist_sources (checklist_id, source, rank, genre, subgenre, list_year)
               VALUES ($1, $2, $3, $4, $5, $6)
               ON CONFLICT (checklist_id, source) DO UPDATE SET
                   rank = EXCLUDED.rank,
                   genre = EXCLUDED.genre,
                   subgenre = EXCLUDED.subgenre,
                   list_year = EXCLUDED.list_year""",
            checklist_id, alb["source"], alb.get("rank"),
            alb.get("genre"), alb.get("subgenre"), alb.get("list_year"),
        )
        linked += 1

    return {"inserted": inserted, "linked": linked, "source": albums[0]["source"] if albums else ""}


async def sync_canonical(conn: asyncpg.Connection) -> dict:
    """Sync canonical_albums into checklist tables."""
    # Check if canonical_albums exists
    exists = await conn.fetchval("SELECT to_regclass('public.canonical_albums')")
    if exists is None:
        return {"synced": 0}

    canonical = await conn.fetch(
        "SELECT id, artist, album, year, genre, subgenre, tier, description FROM canonical_albums"
    )

    synced = 0
    for ca in canonical:
        row = await conn.fetchrow(
            """INSERT INTO checklist_albums (artist, album, year)
               VALUES ($1, $2, $3)
               ON CONFLICT (artist, album) DO NOTHING
               RETURNING id""",
            ca["artist"], ca["album"], ca["year"],
        )
        checklist_id = row["id"] if row else await conn.fetchval(
            "SELECT id FROM checklist_albums WHERE artist = $1 AND album = $2",
            ca["artist"], ca["album"],
        )

        if checklist_id is None:
            continue

        await conn.execute(
            """INSERT INTO checklist_sources
                   (checklist_id, source, genre, subgenre, tier, description)
               VALUES ($1, 'canonical', $2, $3, $4, $5)
               ON CONFLICT (checklist_id, source) DO UPDATE SET
                   genre = EXCLUDED.genre,
                   subgenre = EXCLUDED.subgenre,
                   tier = EXCLUDED.tier,
                   description = EXCLUDED.description""",
            checklist_id, ca["genre"], ca["subgenre"], ca["tier"], ca["description"],
        )
        synced += 1

    return {"synced": synced}


async def run(csv_dir: str | None = None):
    """Main ingestion entry point."""
    csv_dir = csv_dir or os.environ.get("CHECKLIST_CSV_DIR", DEFAULT_CSV_DIR)
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL not set")

    conn = await asyncpg.connect(db_url)
    try:
        # Parse all CSVs
        rs = parse_rolling_stone(csv_dir)
        albums_1001 = parse_1001_albums(csv_dir)
        aoty = parse_aoty(csv_dir)

        # Load each source
        results = []
        for source_albums in [rs, albums_1001, aoty]:
            if source_albums:
                r = await load_albums(conn, source_albums)
                results.append(r)
                print(f"  {r['source']}: {r['inserted']} new albums, {r['linked']} source links")

        # Sync canonical
        canon = await sync_canonical(conn)
        print(f"  canonical: {canon['synced']} synced")

        # Summary
        total = await conn.fetchval("SELECT COUNT(*) FROM checklist_albums")
        sources = await conn.fetch(
            "SELECT source, COUNT(*) AS cnt FROM checklist_sources GROUP BY source ORDER BY source"
        )
        print(f"\nTotal checklist albums: {total}")
        for s in sources:
            print(f"  {s['source']}: {s['cnt']}")

        return {"total": total, "sources": {s["source"]: s["cnt"] for s in sources}}
    finally:
        await conn.close()


if __name__ == "__main__":
    import asyncio

    csv_dir = sys.argv[1] if len(sys.argv) > 1 else None
    logging.basicConfig(level=logging.INFO)
    print("Ingesting album checklists...")
    result = asyncio.run(run(csv_dir))
    print(f"\nDone: {result}")
