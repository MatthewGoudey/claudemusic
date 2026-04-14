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

# Patterns to strip from album names before searching MusicBrainz.
# Spotify/ListenBrainz add these suffixes; MB doesn't use them.
_ALBUM_CLEAN_PATTERNS = [
    re.compile(r'\s*\[([^\]]*)\]\s*$'),           # [Standard Edition], [Deluxe], [Original Game Soundtrack]
    re.compile(r'\s*\(([^)]*(?:edition|remaster|version|deluxe|expanded|anniversary|bonus|mono|stereo|remix|original|complete|soundtrack)[^)]*)\)\s*$', re.IGNORECASE),
    re.compile(r'\s*[-–—]\s*(EP|Single|Deluxe|Remastered|Bonus Track Version)\s*$', re.IGNORECASE),
    re.compile(r'\s*[-–—]\s*\w+(?:\s+\w+)?\s+(?:Edition|Version)\s*$', re.IGNORECASE),  # "- Real Life Edition", "- Deluxe Edition"
    re.compile(r'\s*\(feat\.?\s[^)]*\)\s*$', re.IGNORECASE),
    re.compile(r'\s*\(Complete\)\s*$', re.IGNORECASE),  # "(Complete)"
    re.compile(r'\s+EP\s*$', re.IGNORECASE),  # trailing " EP"
    re.compile(r'\s+b/w\s+.*$', re.IGNORECASE),  # "b/w Other Side" coupling
]

# Wrapping quotes pattern (applied separately to avoid stripping internal quotes)
_QUOTE_WRAP = re.compile(r'^["\'""]+(.+?)["\'""]+$')

# Artist prefix pattern — only used as fallback, never first attempt
_ARTIST_PREFIX = re.compile(r'^(Ms\.?|Mr\.?)\s+', re.IGNORECASE)


def _clean_album_name(album: str) -> str:
    """Strip Spotify/scrobble edition suffixes that prevent MusicBrainz matches."""
    cleaned = album
    # Strip wrapping quotes first
    m = _QUOTE_WRAP.match(cleaned)
    if m:
        cleaned = m.group(1)
    for pattern in _ALBUM_CLEAN_PATTERNS:
        cleaned = pattern.sub('', cleaned)
    return cleaned.strip()


def _clean_artist_name(artist: str) -> str:
    """Strip common artist name prefixes (Ms., Mr.) for MB search fallback."""
    return _ARTIST_PREFIX.sub('', artist).strip()


def _split_artists(artist: str) -> list[str]:
    """Return artist name candidates in priority order.

    For comma-separated strings, returns [original, first_artist].
    Guards against "Tyler, The Creator" by not splitting when the
    post-comma segment starts with The/A/An.
    """
    candidates = [artist]

    # Comma splitting (but not "Tyler, The Creator")
    if ',' in artist:
        parts = [p.strip() for p in artist.split(',', 1)]
        if len(parts) == 2 and not re.match(r'\s*(The|A|An)\s', parts[1], re.IGNORECASE):
            candidates.append(parts[0])

    # Featuring/collaboration splitting
    for sep in (' feat. ', ' feat ', ' ft. ', ' ft ', ' & ', ' and ', ' with ', ' x ', ' vs. ', ' vs '):
        idx = artist.lower().find(sep.lower())
        if idx > 0:
            first = artist[:idx].strip()
            if first and first not in candidates:
                candidates.append(first)
            break

    return candidates


async def _mb_search(
    client: httpx.AsyncClient,
    query: str,
) -> list[dict]:
    """Execute a MusicBrainz release search query. Handles 503 rate limits."""
    params = {"query": query, "fmt": "json", "limit": "25"}
    try:
        resp = await client.get(f"{MB_BASE}/release", params=params)
        if resp.status_code == 503:
            logger.warning("Rate limited by MB, backing off 5s")
            await asyncio.sleep(5)
            resp = await client.get(f"{MB_BASE}/release", params=params)
        resp.raise_for_status()
        return resp.json().get("releases", [])
    except Exception as e:
        logger.error(f"MB API error for query '{query}': {e}")
        return []


def _pick_best_release(
    releases: list[dict],
) -> tuple[int, str] | None:
    """From a list of MB releases, pick the best (track_count, release_type).

    Groups by release-group primary-type, prefers Album > EP > Single.
    Uses MINIMUM non-excluded track count to naturally prefer standard editions
    over deluxe/remaster/box set editions.
    """
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

    for release_type in ("Album", "EP", "Single"):
        for source in (by_type, by_type_unfiltered):
            counts = source.get(release_type, [])
            if release_type == "Album":
                counts = [c for c in counts if c >= 5]
            if not counts:
                continue
            return min(counts), release_type

    return None


async def resolve_album_tracklist(
    client: httpx.AsyncClient,
    artist: str,
    album: str,
) -> tuple[int, str] | None:
    """Query MusicBrainz for an album's track count and release type.

    Uses multi-strategy search: cleaned names, artist variants, unquoted fallbacks.
    Prefers the smallest (standard) edition via min() over median().
    Returns (track_count, release_type) or None.
    """
    clean_album = _clean_album_name(album)
    artist_candidates = _split_artists(artist)

    # Strategy 1: Original artist + cleaned album (exact quoted)
    releases = await _mb_search(client, f'"{clean_album}" AND artist:"{artist}"')
    result = _pick_best_release(releases)
    if result:
        return result

    # Strategy 2: Cleaned artist name (strip Ms., Mr.) + cleaned album
    cleaned_artist = _clean_artist_name(artist)
    if cleaned_artist != artist:
        await asyncio.sleep(1.1)
        releases = await _mb_search(client, f'"{clean_album}" AND artist:"{cleaned_artist}"')
        result = _pick_best_release(releases)
        if result:
            return result

    # Strategy 3: First artist from comma/feat split + cleaned album
    for candidate in artist_candidates[1:]:  # skip index 0, already tried
        await asyncio.sleep(1.1)
        releases = await _mb_search(client, f'"{clean_album}" AND artist:"{candidate}"')
        result = _pick_best_release(releases)
        if result:
            return result

    # Strategy 4: Unquoted search (fuzzy) with original artist
    if clean_album != album:
        await asyncio.sleep(1.1)
        releases = await _mb_search(client, f'{clean_album} AND artist:"{artist}"')
        result = _pick_best_release(releases)
        if result:
            return result

    # Strategy 5: Unquoted search with cleaned artist
    if cleaned_artist != artist:
        await asyncio.sleep(1.1)
        releases = await _mb_search(client, f'{clean_album} AND artist:"{cleaned_artist}"')
        result = _pick_best_release(releases)
        if result:
            return result

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
    # Re-resolve entries missing release_type (but keep tombstones)
    await execute("DELETE FROM album_tracklist WHERE release_type = 'unknown' AND source != 'unresolvable'")

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
                # Insert tombstone so this album is skipped on future runs
                await execute(
                    """INSERT INTO album_tracklist (raw_artist, raw_album, track_count, release_type, source, resolved_at, resolution_notes)
                       VALUES ($1, $2, 0, 'unknown', 'unresolvable', $3, 'No match in MusicBrainz')
                       ON CONFLICT (raw_artist, raw_album) DO NOTHING""",
                    artist, album, datetime.now(timezone.utc),
                )
                failed += 1
                failures.append({"raw_artist": artist, "raw_album": album, "error": "No match found"})
                logger.warning(f"Failed (tombstoned): {artist} - {album}")

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


async def audit_and_reresolve() -> dict:
    """Comprehensive quality audit of all resolved albums.

    Catches THREE types of issues:
    1. tracks_played > track_count (resolver picked standard, user has deluxe)
    2. track_count >> tracks_played on high-listen albums (resolver picked deluxe,
       user has standard — the Doolittle/Demon Days problem)
    3. Box sets (track_count > 30)

    For type 2, re-resolves preferring the edition closest to what the user played.
    """
    # Flag box sets
    await execute("""
        UPDATE album_tracklist SET quality_flag = 'box_set_suspect'
        WHERE track_count > 30 AND release_type = 'Album'
          AND source = 'musicbrainz' AND quality_flag IS NULL
    """)

    # Type 1: user played MORE tracks than resolved (standard→deluxe)
    overshoot = await fetch("""
        SELECT at.raw_artist, at.raw_album, at.track_count,
               COUNT(DISTINCT le.raw_title) as tracks_played,
               COUNT(*) as total_listens
        FROM album_tracklist at
        JOIN listen_events le ON LOWER(at.raw_artist) = LOWER(le.raw_artist)
                              AND LOWER(at.raw_album) = LOWER(le.raw_album)
        WHERE at.source = 'musicbrainz' AND at.quality_flag IS NULL
        GROUP BY at.raw_artist, at.raw_album, at.track_count
        HAVING COUNT(DISTINCT le.raw_title) > at.track_count
        ORDER BY COUNT(*) DESC
    """)

    # Type 2: high-listen albums where user played 70%+ of a SMALLER edition
    # but track_count is inflated (deluxe→standard problem)
    inflated = await fetch("""
        SELECT at.raw_artist, at.raw_album, at.track_count,
               COUNT(DISTINCT le.raw_title) as tracks_played,
               COUNT(*) as total_listens
        FROM album_tracklist at
        JOIN listen_events le ON LOWER(at.raw_artist) = LOWER(le.raw_artist)
                              AND LOWER(at.raw_album) = LOWER(le.raw_album)
        WHERE at.source = 'musicbrainz'
          AND at.quality_flag IS NULL
          AND at.release_type = 'Album'
          AND at.track_count <= 30
        GROUP BY at.raw_artist, at.raw_album, at.track_count
        HAVING COUNT(*) >= 20
           AND COUNT(DISTINCT le.raw_title) < at.track_count
           AND COUNT(DISTINCT le.raw_title) >= 5
           AND COUNT(DISTINCT le.raw_title)::float / at.track_count BETWEEN 0.4 AND 0.85
        ORDER BY COUNT(*) DESC
    """)

    reresolve_count = 0
    improved = 0
    still_mismatched = 0
    inflated_fixed = 0
    inflated_kept = 0

    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=30,
    ) as client:
        # Fix type 1: overshoot — re-resolve preferring editions >= tracks_played
        for row in overshoot:
            artist, album = row["raw_artist"], row["raw_album"]
            old_count, tracks_played = row["track_count"], row["tracks_played"]

            await asyncio.sleep(1.1)
            result = await resolve_album_tracklist(
                client, artist, album
            )
            reresolve_count += 1

            if result and result[0] != old_count:
                track_count, release_type = result
                await execute(
                    """UPDATE album_tracklist
                       SET track_count = $3, release_type = $4, resolved_at = $5,
                           quality_flag = NULL, resolution_notes = $6
                       WHERE raw_artist = $1 AND raw_album = $2""",
                    artist, album, track_count, release_type,
                    datetime.now(timezone.utc),
                    f"Audit overshoot: {old_count} -> {track_count} (user played {tracks_played})",
                )
                await execute(
                    "DELETE FROM album_sessions WHERE LOWER(raw_artist) = LOWER($1) AND LOWER(raw_album) = LOWER($2)",
                    artist, album,
                )
                improved += 1
            else:
                await execute(
                    """UPDATE album_tracklist SET quality_flag = 'track_count_mismatch',
                       resolution_notes = $3
                       WHERE raw_artist = $1 AND raw_album = $2""",
                    artist, album,
                    f"User played {tracks_played} unique tracks vs {old_count} resolved",
                )
                still_mismatched += 1

        # Fix type 2: inflated — re-resolve preferring editions closest to tracks_played
        for row in inflated:
            artist, album = row["raw_artist"], row["raw_album"]
            old_count, tracks_played = row["track_count"], row["tracks_played"]

            await asyncio.sleep(1.1)
            # Search MB for all editions
            clean_album = _clean_album_name(album)
            releases = await _mb_search(client, f'"{clean_album}" AND artist:"{artist}"')

            # Find all Album-type track counts
            album_counts = []
            for rel in releases:
                rg = rel.get("release-group", {})
                if rg.get("primary-type") != "Album":
                    continue
                tc = sum(m.get("track-count", 0) for m in rel.get("media", []))
                if tc >= 5:
                    album_counts.append(tc)

            reresolve_count += 1

            if not album_counts:
                inflated_kept += 1
                continue

            # Pick the edition closest to (but >= ) tracks_played
            # This prefers standard editions when user played standard track count
            candidates = sorted(set(album_counts))
            best = None
            for tc in candidates:
                if tc >= tracks_played:
                    best = tc
                    break
            if best is None:
                best = max(candidates)  # fallback to largest if none covers

            if best != old_count:
                await execute(
                    """UPDATE album_tracklist
                       SET track_count = $3, resolved_at = $4,
                           quality_flag = NULL, resolution_notes = $5
                       WHERE raw_artist = $1 AND raw_album = $2""",
                    artist, album, best,
                    datetime.now(timezone.utc),
                    f"Audit inflated: {old_count} -> {best} (user played {tracks_played}, editions: {candidates})",
                )
                await execute(
                    "DELETE FROM album_sessions WHERE LOWER(raw_artist) = LOWER($1) AND LOWER(raw_album) = LOWER($2)",
                    artist, album,
                )
                inflated_fixed += 1
                logger.info(f"Deflated: {artist} - {album}: {old_count} -> {best} (played {tracks_played})")
            else:
                inflated_kept += 1

    return {
        "overshoot_found": len(overshoot),
        "inflated_found": len(inflated),
        "reresolve_attempted": reresolve_count,
        "overshoot_improved": improved,
        "overshoot_kept": still_mismatched,
        "inflated_fixed": inflated_fixed,
        "inflated_kept": inflated_kept,
    }
