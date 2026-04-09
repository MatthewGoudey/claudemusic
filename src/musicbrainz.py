"""
MusicBrainz tracklist resolver.

Resolves album track counts from MusicBrainz for album completion calculations.
Rate limited to 1 request/second per MB API terms.
"""

import re
import asyncio
import logging
import statistics
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
) -> tuple[int, str] | None:
    """Query MusicBrainz for an album's track count and release type.

    Searches the release endpoint, groups results by release-group primary-type,
    prefers Album > EP > Single. Returns (track_count, release_type) or None.
    """
    query = f'"{album}" AND artist:"{artist}"'
    params = {"query": query, "fmt": "json", "limit": "25"}

    try:
        resp = await client.get(f"{MB_BASE}/release", params=params)
        if resp.status_code == 503:
            logger.warning("Rate limited by MB, backing off 5s")
            await asyncio.sleep(5)
            resp = await client.get(f"{MB_BASE}/release", params=params)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.error(f"MB API error for '{artist}' - '{album}': {e}")
        return None

    releases = data.get("releases", [])
    if not releases:
        return None

    # Group track counts by release type, preferring Album > EP > Single
    by_type: dict[str, list[int]] = {}
    by_type_unfiltered: dict[str, list[int]] = {}

    for release in releases:
        rg = release.get("release-group", {})
        primary_type = rg.get("primary-type", "")
        if not primary_type:
            continue

        media = release.get("media", [])
        total = sum(m.get("track-count", 0) for m in media)
        if total <= 0:
            continue

        title = release.get("title", "")
        by_type_unfiltered.setdefault(primary_type, []).append(total)

        if EDITION_EXCLUDE.search(title):
            continue
        by_type.setdefault(primary_type, []).append(total)

    # Pick best type in preference order
    for release_type in ("Album", "EP", "Single"):
        counts = by_type.get(release_type, [])
        if release_type == "Album":
            counts = [c for c in counts if c >= 5]
        if counts:
            return int(statistics.median(counts)), release_type

        counts = by_type_unfiltered.get(release_type, [])
        if release_type == "Album":
            counts = [c for c in counts if c >= 5]
        if counts:
            return int(statistics.median(counts)), release_type

    return None


async def resolve_single(artist: str, album: str) -> dict:
    """Resolve a single artist+album pair and store the result."""
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=30,
    ) as client:
        result = await resolve_album_tracklist(client, artist, album)

    if result is not None:
        track_count, release_type = result
        await execute(
            """INSERT INTO album_tracklist (raw_artist, raw_album, track_count, release_type, source, resolved_at)
               VALUES ($1, $2, $3, $4, 'musicbrainz', $5)
               ON CONFLICT (raw_artist, raw_album)
               DO UPDATE SET track_count = $3, release_type = $4, resolved_at = $5""",
            artist, album, track_count, release_type, datetime.now(timezone.utc),
        )
        # Invalidate precomputed sessions
        await execute(
            "DELETE FROM album_sessions WHERE LOWER(raw_artist) = LOWER($1) AND LOWER(raw_album) = LOWER($2)",
            artist, album,
        )
        return {"raw_artist": artist, "raw_album": album, "track_count": track_count, "release_type": release_type, "status": "resolved"}
    else:
        return {"raw_artist": artist, "raw_album": album, "error": "No match found", "status": "failed"}


async def resolve_all_missing() -> dict:
    """Resolve all albums in listen_events that aren't in album_tracklist."""
    # Re-resolve entries missing release_type
    await execute("DELETE FROM album_tracklist WHERE release_type = 'unknown'")

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
            result = await resolve_album_tracklist(client, artist, album)

            if result is not None:
                track_count, release_type = result
                await execute(
                    """INSERT INTO album_tracklist (raw_artist, raw_album, track_count, release_type, source, resolved_at)
                       VALUES ($1, $2, $3, $4, 'musicbrainz', $5)
                       ON CONFLICT (raw_artist, raw_album)
                       DO UPDATE SET track_count = $3, release_type = $4, resolved_at = $5""",
                    artist, album, track_count, release_type, datetime.now(timezone.utc),
                )
                await execute(
                    "DELETE FROM album_sessions WHERE LOWER(raw_artist) = LOWER($1) AND LOWER(raw_album) = LOWER($2)",
                    artist, album,
                )
                resolved += 1
                logger.info(f"Resolved: {artist} - {album} ({track_count} tracks, {release_type})")
            else:
                failed += 1
                failures.append({"raw_artist": artist, "raw_album": album, "error": "No match found"})
                logger.warning(f"Failed: {artist} - {album}")

    return {
        "resolved": resolved,
        "failed": failed,
        "already_cached": already_cached,
        "failures": failures[:50],
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
