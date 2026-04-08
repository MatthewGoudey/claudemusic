"""
MusicBrainz tracklist resolver.

Resolves album track counts from MusicBrainz for album completion calculations.
Rate limited to 1 request/second per MB API terms.
"""

import re
import asyncio
import logging
from datetime import datetime, timezone

import httpx

from src.database import fetch, execute, fetchval

logger = logging.getLogger(__name__)

MB_BASE = "https://musicbrainz.org/ws/2"
USER_AGENT = "MusicListeningPipeline/0.2 (personal project; https://github.com)"

# Patterns to exclude deluxe/expanded editions
EDITION_EXCLUDE = re.compile(
    r"(deluxe|expanded|anniversary|bonus|special\s+edition|collector|remaster|live)",
    re.IGNORECASE,
)


async def resolve_album_tracklist(
    client: httpx.AsyncClient,
    artist: str,
    album: str,
) -> int | None:
    """Query MusicBrainz for an album's track count.

    Returns the lowest track count among standard (non-deluxe) releases,
    or None if no match found.
    """
    query = f'release:{album} AND artist:{artist}'
    params = {"query": query, "fmt": "json", "limit": "10"}

    try:
        resp = await client.get(f"{MB_BASE}/release-group", params=params)
        if resp.status_code == 503:
            logger.warning("Rate limited by MB, backing off 5s")
            await asyncio.sleep(5)
            resp = await client.get(f"{MB_BASE}/release-group", params=params)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error(f"MB API error for '{artist}' - '{album}': {e}")
        return None

    release_groups = data.get("release-groups", [])
    if not release_groups:
        return None

    # Take the top-scoring release group
    rg = release_groups[0]
    rg_id = rg.get("id")
    if not rg_id:
        return None

    # Get releases for this release group
    await asyncio.sleep(1.1)  # Rate limit
    try:
        resp = await client.get(
            f"{MB_BASE}/release",
            params={"release-group": rg_id, "fmt": "json", "limit": "50", "inc": "media"},
        )
        resp.raise_for_status()
        releases_data = resp.json()
    except Exception as e:
        logger.error(f"MB API error fetching releases for rg {rg_id}: {e}")
        return None

    releases = releases_data.get("releases", [])
    if not releases:
        return None

    # Filter out deluxe/expanded editions and find lowest track count
    track_counts = []
    for release in releases:
        title = release.get("title", "")
        if EDITION_EXCLUDE.search(title):
            continue
        media = release.get("media", [])
        total = sum(m.get("track-count", 0) for m in media)
        if total > 0:
            track_counts.append(total)

    # If all were excluded, try unfiltered
    if not track_counts:
        for release in releases:
            media = release.get("media", [])
            total = sum(m.get("track-count", 0) for m in media)
            if total > 0:
                track_counts.append(total)

    return min(track_counts) if track_counts else None


async def resolve_single(artist: str, album: str) -> dict:
    """Resolve a single artist+album pair and store the result."""
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=30,
    ) as client:
        track_count = await resolve_album_tracklist(client, artist, album)

    if track_count is not None:
        await execute(
            """INSERT INTO album_tracklist (raw_artist, raw_album, track_count, source, resolved_at)
               VALUES ($1, $2, $3, 'musicbrainz', $4)
               ON CONFLICT (raw_artist, raw_album)
               DO UPDATE SET track_count = $3, resolved_at = $4""",
            artist, album, track_count, datetime.now(timezone.utc),
        )
        return {"raw_artist": artist, "raw_album": album, "track_count": track_count, "status": "resolved"}
    else:
        return {"raw_artist": artist, "raw_album": album, "error": "No match found", "status": "failed"}


async def resolve_all_missing() -> dict:
    """Resolve all albums in listen_events that aren't in album_tracklist."""
    # Find unresolved albums
    unresolved = await fetch("""
        SELECT DISTINCT le.raw_artist, le.raw_album
        FROM listen_events le
        LEFT JOIN album_tracklist at ON LOWER(le.raw_artist) = LOWER(at.raw_artist)
                                    AND LOWER(le.raw_album) = LOWER(at.raw_album)
        WHERE le.raw_album IS NOT NULL
          AND at.raw_artist IS NULL
        ORDER BY le.raw_artist, le.raw_album
    """)

    already_cached = await fetchval("""SELECT COUNT(*) FROM album_tracklist""")

    resolved = 0
    failed = 0
    failures = []

    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=30,
    ) as client:
        for row in unresolved:
            artist = row["raw_artist"]
            album = row["raw_album"]

            await asyncio.sleep(1.1)  # Rate limit
            track_count = await resolve_album_tracklist(client, artist, album)

            if track_count is not None:
                await execute(
                    """INSERT INTO album_tracklist (raw_artist, raw_album, track_count, source, resolved_at)
                       VALUES ($1, $2, $3, 'musicbrainz', $4)
                       ON CONFLICT (raw_artist, raw_album)
                       DO UPDATE SET track_count = $3, resolved_at = $4""",
                    artist, album, track_count, datetime.now(timezone.utc),
                )
                resolved += 1
                logger.info(f"Resolved: {artist} - {album} ({track_count} tracks)")
            else:
                failed += 1
                failures.append({"raw_artist": artist, "raw_album": album, "error": "No match found"})
                logger.warning(f"Failed: {artist} - {album}")

    return {
        "resolved": resolved,
        "failed": failed,
        "already_cached": already_cached,
        "failures": failures[:50],  # Cap failure list
    }


async def get_resolution_status() -> dict:
    """Check how many albums are resolved vs unresolved."""
    total = await fetchval("""
        SELECT COUNT(DISTINCT (raw_artist, raw_album))
        FROM listen_events
        WHERE raw_album IS NOT NULL
    """)
    resolved = await fetchval("""SELECT COUNT(*) FROM album_tracklist""")
    unresolved = total - resolved if total else 0
    rate = round(resolved / total, 3) if total else 0.0

    return {
        "total_albums": total,
        "resolved": resolved,
        "unresolved": unresolved,
        "resolution_rate": rate,
    }
