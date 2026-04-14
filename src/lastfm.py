"""
Last.fm fallback resolver for album tracklists.

Used when MusicBrainz doesn't have an album. Less reliable but broader coverage,
especially for niche/indie releases, game soundtracks, and Bandcamp albums.
"""

import os
import asyncio
import logging
from datetime import datetime, timezone

import httpx

from src.database import fetch, execute, fetchval

logger = logging.getLogger(__name__)

LASTFM_API_KEY = os.environ.get("LASTFM_API_KEY", "")
LASTFM_BASE = "https://ws.audioscrobbler.com/2.0/"


async def resolve_album_lastfm(
    client: httpx.AsyncClient,
    artist: str,
    album: str,
) -> tuple[int, str] | None:
    """Query Last.fm album.getInfo for track count.

    Returns (track_count, release_type) or None.
    Last.fm doesn't reliably distinguish Albums/EPs/Singles,
    so we infer from track count: >=5 = Album, 2-4 = EP, 1 = Single.
    """
    if not LASTFM_API_KEY:
        logger.warning("LASTFM_API_KEY not set, skipping Last.fm fallback")
        return None

    params = {
        "method": "album.getInfo",
        "artist": artist,
        "album": album,
        "api_key": LASTFM_API_KEY,
        "format": "json",
    }

    try:
        resp = await client.get(LASTFM_BASE, params=params)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error(f"Last.fm API error for '{artist}' - '{album}': {e}")
        return None

    if "error" in data:
        return None

    album_data = data.get("album", {})
    tracks = album_data.get("tracks", {}).get("track", [])

    # Last.fm returns a dict instead of a list when there's only 1 track
    if isinstance(tracks, dict):
        tracks = [tracks]

    if not tracks:
        return None

    track_count = len(tracks)

    # Infer release type from track count
    if track_count >= 5:
        release_type = "Album"
    elif track_count >= 2:
        release_type = "EP"
    else:
        release_type = "Single"

    return track_count, release_type


async def resolve_missing_via_lastfm(limit: int = 500) -> dict:
    """Resolve albums that failed MusicBrainz using Last.fm as fallback.

    Only targets albums that are in listen_events but NOT in album_tracklist.
    Processes up to `limit` albums per run.
    """
    if not LASTFM_API_KEY:
        return {"error": "LASTFM_API_KEY not set"}

    # Find unresolved albums, ordered by listen count (highest priority first)
    unresolved = await fetch("""
        SELECT u.raw_artist, u.raw_album, u.listens FROM (
            SELECT le.raw_artist, le.raw_album, COUNT(*) as listens
            FROM listen_events le
            LEFT JOIN album_tracklist at ON LOWER(le.raw_artist) = LOWER(at.raw_artist)
                                        AND LOWER(le.raw_album) = LOWER(at.raw_album)
            WHERE le.raw_album IS NOT NULL
              AND at.raw_artist IS NULL
            GROUP BY le.raw_artist, le.raw_album
        ) u
        ORDER BY u.listens DESC
        LIMIT $1
    """, limit)

    resolved = 0
    failed = 0
    failures = []

    async with httpx.AsyncClient(timeout=15) as client:
        for row in unresolved:
            artist = row["raw_artist"]
            album = row["raw_album"]

            await asyncio.sleep(0.25)  # Last.fm rate limit is more generous
            result = await resolve_album_lastfm(client, artist, album)

            if result is not None:
                track_count, release_type = result
                await execute(
                    """INSERT INTO album_tracklist (raw_artist, raw_album, track_count, release_type, source, resolved_at)
                       VALUES ($1, $2, $3, $4, 'lastfm', $5)
                       ON CONFLICT (raw_artist, raw_album)
                       DO UPDATE SET track_count = $3, release_type = $4, source = 'lastfm', resolved_at = $5""",
                    artist, album, track_count, release_type, datetime.now(timezone.utc),
                )
                # Invalidate precomputed sessions
                await execute(
                    "DELETE FROM album_sessions WHERE LOWER(raw_artist) = LOWER($1) AND LOWER(raw_album) = LOWER($2)",
                    artist, album,
                )
                resolved += 1
                logger.info(f"Last.fm resolved: {artist} - {album} ({track_count} tracks, {release_type})")
            else:
                failed += 1
                failures.append({"raw_artist": artist, "raw_album": album})

    return {
        "source": "lastfm",
        "resolved": resolved,
        "failed": failed,
        "failures": failures[:50],
    }
