"""
Music Pipeline API — async read-only endpoints backed by asyncpg connection pool.

Usage:
    uvicorn api:app --reload --port 8000
"""

import asyncio
import logging
from datetime import date, timedelta
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Query
from pydantic import BaseModel

from src.database import get_pool, get_ro_pool, close_pools, fetch, fetchrow, fetchval, fetch_ro, execute
from src.auth import verify_key
from src.filters import parse_date_filter, DateFilter
from src.musicbrainz import resolve_single, resolve_all_missing, get_resolution_status
from src.schema_registry import SCHEMA
import math

logger = logging.getLogger(__name__)

app = FastAPI(title="Music Listening API", version="0.2.0")


@app.on_event("startup")
async def startup():
    pool = await get_pool()
    # Ensure chicago_shows table exists
    async with pool.acquire() as conn:
        exists = await conn.fetchval("SELECT to_regclass('public.chicago_shows')")
        if exists is None:
            import os
            sql_path = os.path.join(os.path.dirname(__file__), "sql", "chicago_shows.sql")
            with open(sql_path) as f:
                await conn.execute(f.read())
            logger.info("Created chicago_shows table")
        # Always update the normalize_artist function (fixes LOWER() ordering)
        await conn.execute("""
            CREATE OR REPLACE FUNCTION normalize_artist(name TEXT) RETURNS TEXT AS $$
                SELECT TRIM(REGEXP_REPLACE(
                    REGEXP_REPLACE(
                        REGEXP_REPLACE(LOWER(name), '^\s*the\s+', '', ''),
                        '\s*(feat\.?|ft\.?|with|&|and)\s+.*$', '', ''
                    ), '[^a-z0-9\s]', '', 'g'
                ))
            $$ LANGUAGE SQL IMMUTABLE
        """)
        # Ensure artist_listen_stats table exists
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS artist_listen_stats (
                norm_artist   TEXT PRIMARY KEY,
                raw_artist    TEXT NOT NULL,
                total_listens INTEGER NOT NULL,
                unique_tracks INTEGER NOT NULL,
                unique_albums INTEGER NOT NULL,
                last_listen   TIMESTAMPTZ,
                listens_90d   INTEGER NOT NULL DEFAULT 0,
                listens_365d  INTEGER NOT NULL DEFAULT 0,
                top_album     TEXT,
                computed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)


@app.on_event("shutdown")
async def shutdown():
    await close_pools()


# ============================================================
# Helpers
# ============================================================

def _df(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    days: Optional[int] = None,
    limit: int = 50,
) -> DateFilter:
    return parse_date_filter(start_date, end_date, days, limit)


async def _get_album_track_info(artist: str, album: str) -> tuple[int, str, str]:
    """Get track count, source, and release type from album_tracklist or heuristic.

    Returns (track_count, tracklist_source, release_type).
    """
    row = await fetchrow(
        "SELECT track_count, release_type FROM album_tracklist WHERE LOWER(raw_artist) = LOWER($1) AND LOWER(raw_album) = LOWER($2)",
        artist, album,
    )
    if row:
        return row["track_count"], "musicbrainz", row.get("release_type", "unknown")

    count = await fetchval(
        "SELECT COUNT(DISTINCT raw_title) FROM listen_events WHERE LOWER(raw_artist) = LOWER($1) AND LOWER(raw_album) = LOWER($2)",
        artist, album,
    )
    return count or 0, "heuristic", "unknown"


# ============================================================
# Health + Schema (no auth)
# ============================================================

@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/schema")
async def schema():
    return SCHEMA


# ============================================================
# Summary
# ============================================================

@app.get("/api/summary")
async def summary(_=Depends(verify_key)):
    rows = await fetch("""
        SELECT
            COUNT(*) AS total_listens,
            COUNT(DISTINCT raw_title || '|||' || raw_artist) AS unique_tracks,
            COUNT(DISTINCT raw_artist) AS unique_artists,
            MIN(listened_at) AS earliest_listen,
            MAX(listened_at) AS latest_listen,
            COUNT(*) FILTER (WHERE is_resolved) AS resolved_listens,
            COUNT(DISTINCT platform) AS platform_count
        FROM listen_events
    """)
    platform_breakdown = await fetch("""
        SELECT platform, source_system, COUNT(*) AS count
        FROM listen_events
        GROUP BY platform, source_system
        ORDER BY count DESC
    """)
    return {
        "overview": rows[0] if rows else {},
        "platform_breakdown": platform_breakdown,
    }


# ============================================================
# Top artists
# ============================================================

@app.get("/api/top-artists")
async def top_artists(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    days: Optional[int] = None,
    limit: int = Query(default=50, le=1000),
    _=Depends(verify_key),
):
    df = _df(start_date, end_date, days, limit)
    clauses, params = df.build_where()
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    idx = len(params) + 1

    rows = await fetch(f"""
        SELECT
            raw_artist,
            COUNT(*) AS listen_count,
            COUNT(DISTINCT raw_title) AS unique_tracks,
            COUNT(DISTINCT raw_album) FILTER (WHERE raw_album IS NOT NULL) AS unique_albums,
            MIN(listened_at) AS first_listen,
            MAX(listened_at) AS last_listen
        FROM listen_events
        {where}
        GROUP BY raw_artist
        ORDER BY listen_count DESC
        LIMIT ${idx}
    """, *params, df.limit)
    return {"artists": rows, "filters": df.as_dict()}


# ============================================================
# Top tracks
# ============================================================

@app.get("/api/top-tracks")
async def top_tracks(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    days: Optional[int] = None,
    artist: Optional[str] = None,
    limit: int = Query(default=50, le=1000),
    _=Depends(verify_key),
):
    df = _df(start_date, end_date, days, limit)
    clauses, params = df.build_where()

    if artist:
        idx = len(params) + 1
        clauses.append(f"LOWER(raw_artist) = LOWER(${idx})")
        params.append(artist)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    idx = len(params) + 1

    rows = await fetch(f"""
        SELECT
            raw_title,
            raw_artist,
            raw_album,
            COUNT(*) AS listen_count,
            MIN(listened_at) AS first_listen,
            MAX(listened_at) AS last_listen
        FROM listen_events
        {where}
        GROUP BY raw_title, raw_artist, raw_album
        ORDER BY listen_count DESC
        LIMIT ${idx}
    """, *params, df.limit)
    return {"tracks": rows, "filters": {**df.as_dict(), "artist": artist}}


# ============================================================
# Top albums
# ============================================================

@app.get("/api/top-albums")
async def top_albums(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    days: Optional[int] = None,
    artist: Optional[str] = None,
    limit: int = Query(default=50, le=1000),
    _=Depends(verify_key),
):
    df = _df(start_date, end_date, days, limit)
    clauses, params = df.build_where()
    clauses.append("raw_album IS NOT NULL")

    if artist:
        idx = len(params) + 1
        clauses.append(f"LOWER(raw_artist) = LOWER(${idx})")
        params.append(artist)

    where = "WHERE " + " AND ".join(clauses)
    idx = len(params) + 1

    rows = await fetch(f"""
        SELECT
            raw_album,
            raw_artist,
            COUNT(*) AS listen_count,
            COUNT(DISTINCT raw_title) AS unique_tracks,
            MIN(listened_at) AS first_listen,
            MAX(listened_at) AS last_listen
        FROM listen_events
        {where}
        GROUP BY raw_album, raw_artist
        ORDER BY listen_count DESC
        LIMIT ${idx}
    """, *params, df.limit)
    return {"albums": rows, "filters": {**df.as_dict(), "artist": artist}}


# ============================================================
# Timeline
# ============================================================

@app.get("/api/timeline")
async def timeline(
    granularity: str = Query(default="month", pattern="^(day|week|month|year)$"),
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    days: Optional[int] = None,
    _=Depends(verify_key),
):
    df = _df(start_date, end_date, days, limit=50)  # limit not used for timeline
    clauses, params = df.build_where()
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    rows = await fetch(f"""
        SELECT
            DATE_TRUNC('{granularity}', listened_at) AS period,
            COUNT(*) AS listen_count,
            COUNT(DISTINCT raw_artist) AS unique_artists,
            COUNT(DISTINCT raw_title || '|||' || raw_artist) AS unique_tracks
        FROM listen_events
        {where}
        GROUP BY period
        ORDER BY period
    """, *params)
    return {"timeline": rows, "granularity": granularity, "filters": df.as_dict()}


# ============================================================
# Recent listens
# ============================================================

@app.get("/api/recent")
async def recent(
    limit: int = Query(default=50, le=500),
    _=Depends(verify_key),
):
    rows = await fetch("""
        SELECT raw_title, raw_artist, raw_album, platform, source_app,
               listened_at, content_type, confidence
        FROM listen_events
        ORDER BY listened_at DESC
        LIMIT $1
    """, limit)
    return {"listens": rows}


# ============================================================
# Artist deep dive
# ============================================================

@app.get("/api/artist/{artist_name}")
async def artist_detail(artist_name: str, _=Depends(verify_key)):
    listens = await fetch("""
        SELECT COUNT(*) AS total_listens,
               COUNT(DISTINCT raw_title) AS unique_tracks,
               COUNT(DISTINCT raw_album) FILTER (WHERE raw_album IS NOT NULL) AS unique_albums,
               MIN(listened_at) AS first_listen,
               MAX(listened_at) AS last_listen
        FROM listen_events
        WHERE LOWER(raw_artist) = LOWER($1)
    """, artist_name)

    top_tracks = await fetch("""
        SELECT raw_title, raw_album, COUNT(*) AS listen_count
        FROM listen_events
        WHERE LOWER(raw_artist) = LOWER($1)
        GROUP BY raw_title, raw_album
        ORDER BY listen_count DESC
        LIMIT 20
    """, artist_name)

    by_year = await fetch("""
        SELECT EXTRACT(YEAR FROM listened_at)::int AS year,
               COUNT(*) AS listen_count
        FROM listen_events
        WHERE LOWER(raw_artist) = LOWER($1)
        GROUP BY year
        ORDER BY year
    """, artist_name)

    return {
        "artist": artist_name,
        "stats": listens[0] if listens else {},
        "top_tracks": top_tracks,
        "by_year": by_year,
    }


# ============================================================
# Album completion
# ============================================================

@app.get("/api/album-completion")
async def album_completion(
    artist: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    days: Optional[int] = None,
    min_completion: Optional[float] = None,
    max_completion: Optional[float] = None,
    limit: int = Query(default=50, le=1000),
    _=Depends(verify_key),
):
    df = _df(start_date, end_date, days, limit)
    clauses, params = df.build_where()
    clauses.append("le.raw_album IS NOT NULL")

    if artist:
        idx = len(params) + 1
        clauses.append(f"LOWER(le.raw_artist) = LOWER(${idx})")
        params.append(artist)

    where = "WHERE " + " AND ".join(clauses)

    # Get distinct albums with their heard tracks and listen counts
    albums = await fetch(f"""
        SELECT
            le.raw_artist,
            le.raw_album,
            COUNT(DISTINCT le.raw_title) AS tracks_heard,
            COUNT(*) AS total_listens,
            MIN(le.listened_at) AS first_listen,
            MAX(le.listened_at) AS last_listen
        FROM listen_events le
        {where}
        GROUP BY le.raw_artist, le.raw_album
        ORDER BY total_listens DESC
    """, *params)

    results = []
    for album in albums:
        total_tracks, source, _ = await _get_album_track_info(album["raw_artist"], album["raw_album"])
        if total_tracks == 0:
            continue

        completion = round(album["tracks_heard"] / total_tracks, 2)

        if min_completion is not None and completion < min_completion:
            continue
        if max_completion is not None and completion > max_completion:
            continue

        results.append({
            "raw_artist": album["raw_artist"],
            "raw_album": album["raw_album"],
            "tracks_heard": album["tracks_heard"],
            "total_tracks": total_tracks,
            "completion": completion,
            "total_listens": album["total_listens"],
            "first_listen": album["first_listen"].isoformat() if album["first_listen"] else None,
            "last_listen": album["last_listen"].isoformat() if album["last_listen"] else None,
            "tracklist_source": source,
        })

        if len(results) >= df.limit:
            break

    return {
        "albums": results,
        "filters": {
            **df.as_dict(),
            "artist": artist,
            "min_completion": min_completion,
            "max_completion": max_completion,
        },
    }


# ============================================================
# Album sessions (precomputed)
# ============================================================

@app.get("/api/album-sessions")
async def album_sessions(
    artist: Optional[str] = None,
    album: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    days: Optional[int] = None,
    min_completion: Optional[float] = Query(default=0.0),
    session_type: Optional[str] = Query(default=None, pattern="^(full|partial)$"),
    release_type: Optional[str] = Query(default=None, pattern="^(Album|EP|Single)$"),
    limit: int = Query(default=50, le=1000),
    _=Depends(verify_key),
):
    df = _df(start_date, end_date, days, limit)
    clauses, params = df.build_where(col="session_start")

    if artist:
        idx = len(params) + 1
        clauses.append(f"LOWER(raw_artist) = LOWER(${idx})")
        params.append(artist)
    if album:
        idx = len(params) + 1
        clauses.append(f"LOWER(raw_album) = LOWER(${idx})")
        params.append(album)
    if min_completion is not None and min_completion > 0:
        idx = len(params) + 1
        clauses.append(f"completion >= ${idx}")
        params.append(min_completion)
    if session_type:
        idx = len(params) + 1
        clauses.append(f"session_type = ${idx}")
        params.append(session_type)
    if release_type:
        idx = len(params) + 1
        clauses.append(f"release_type = ${idx}")
        params.append(release_type)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    idx = len(params) + 1

    rows = await fetch(f"""
        SELECT raw_artist, raw_album, session_start, session_end,
               tracks_played, total_tracks, completion, session_type,
               release_type, tracklist_source
        FROM album_sessions
        {where}
        ORDER BY session_start DESC
        LIMIT ${idx}
    """, *params, df.limit)

    return {
        "sessions": rows,
        "filters": {
            **df.as_dict(),
            "artist": artist,
            "album": album,
            "min_completion": min_completion,
            "session_type": session_type,
            "release_type": release_type,
        },
    }


# ============================================================
# Album sessions stats
# ============================================================

@app.get("/api/album-sessions/stats")
async def album_sessions_stats(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    days: Optional[int] = None,
    session_type: Optional[str] = Query(default=None, pattern="^(full|partial)$"),
    release_type: Optional[str] = Query(default=None, pattern="^(Album|EP|Single)$"),
    _=Depends(verify_key),
):
    df = _df(start_date, end_date, days, limit=50)
    clauses, params = df.build_where(col="session_start")

    if session_type:
        idx = len(params) + 1
        clauses.append(f"session_type = ${idx}")
        params.append(session_type)
    if release_type:
        idx = len(params) + 1
        clauses.append(f"release_type = ${idx}")
        params.append(release_type)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    totals = await fetchrow(f"""
        SELECT
            COUNT(*) AS total_sessions,
            COUNT(*) FILTER (WHERE session_type = 'full') AS full_sessions,
            COUNT(*) FILTER (WHERE session_type = 'partial') AS partial_sessions,
            COUNT(DISTINCT (raw_artist, raw_album)) AS unique_albums
        FROM album_sessions
        {where}
    """, *params)

    by_year = await fetch(f"""
        SELECT
            EXTRACT(YEAR FROM session_start)::int AS year,
            COUNT(*) FILTER (WHERE session_type = 'full') AS full,
            COUNT(*) FILTER (WHERE session_type = 'partial') AS partial
        FROM album_sessions
        {where}
        GROUP BY year
        ORDER BY year DESC
    """, *params)

    top_albums = await fetch(f"""
        SELECT
            raw_artist, raw_album,
            COUNT(*) FILTER (WHERE session_type = 'full') AS full_sessions,
            COUNT(*) FILTER (WHERE session_type = 'partial') AS partial_sessions
        FROM album_sessions
        {where}
        GROUP BY raw_artist, raw_album
        ORDER BY full_sessions DESC
        LIMIT 25
    """, *params)

    return {
        **(totals or {}),
        "by_year": by_year,
        "top_albums": top_albums,
        "filters": {
            **df.as_dict(),
            "session_type": session_type,
            "release_type": release_type,
        },
    }


# ============================================================
# Artists batch
# ============================================================

class ArtistBatchRequest(BaseModel):
    artists: list[str]


@app.post("/api/artists/batch")
async def artists_batch(body: ArtistBatchRequest, _=Depends(verify_key)):
    results = []

    for artist_name in body.artists:
        # Basic stats
        stats = await fetchrow("""
            SELECT COUNT(*) AS total_listens,
                   COUNT(DISTINCT raw_title) AS unique_tracks,
                   COUNT(DISTINCT raw_album) FILTER (WHERE raw_album IS NOT NULL) AS unique_albums,
                   MIN(listened_at) AS first_listen,
                   MAX(listened_at) AS last_listen
            FROM listen_events
            WHERE LOWER(raw_artist) = LOWER($1)
        """, artist_name)

        if not stats or stats["total_listens"] == 0:
            results.append({
                "artist": artist_name,
                "total_listens": 0,
                "unique_tracks": 0,
                "unique_albums": 0,
                "first_listen": None,
                "last_listen": None,
                "albums": [],
            })
            continue

        # Get albums for this artist
        albums = await fetch("""
            SELECT raw_album,
                   COUNT(DISTINCT raw_title) AS tracks_heard,
                   COUNT(*) AS listen_count
            FROM listen_events
            WHERE LOWER(raw_artist) = LOWER($1) AND raw_album IS NOT NULL
            GROUP BY raw_album
            ORDER BY listen_count DESC
        """, artist_name)

        # Get session counts per album from precomputed table
        session_counts = await fetch("""
            SELECT raw_album,
                   COUNT(*) FILTER (WHERE session_type = 'full') AS full_sessions,
                   COUNT(*) FILTER (WHERE session_type = 'partial') AS partial_sessions
            FROM album_sessions
            WHERE LOWER(raw_artist) = LOWER($1)
            GROUP BY raw_album
        """, artist_name)
        session_map = {r["raw_album"]: r for r in session_counts}

        album_results = []
        for alb in albums:
            total_tracks, _, rel_type = await _get_album_track_info(artist_name, alb["raw_album"])
            completion = round(alb["tracks_heard"] / total_tracks, 2) if total_tracks > 0 else 0.0

            sc = session_map.get(alb["raw_album"], {})

            album_results.append({
                "raw_album": alb["raw_album"],
                "tracks_heard": alb["tracks_heard"],
                "total_tracks": total_tracks,
                "completion": completion,
                "listen_count": alb["listen_count"],
                "release_type": rel_type,
                "full_sessions": sc.get("full_sessions", 0),
                "partial_sessions": sc.get("partial_sessions", 0),
            })

        results.append({
            "artist": artist_name,
            "total_listens": stats["total_listens"],
            "unique_tracks": stats["unique_tracks"],
            "unique_albums": stats["unique_albums"],
            "first_listen": stats["first_listen"].isoformat() if stats["first_listen"] else None,
            "last_listen": stats["last_listen"].isoformat() if stats["last_listen"] else None,
            "albums": album_results,
        })

    return {"results": results}


# ============================================================
# Album tracklist resolver
# ============================================================

class ResolverRequest(BaseModel):
    artist: Optional[str] = None
    album: Optional[str] = None


@app.post("/api/album-tracklist-resolver")
async def trigger_resolver(body: ResolverRequest = ResolverRequest(), _=Depends(verify_key)):
    if body.artist and body.album:
        result = await resolve_single(body.artist, body.album)
        return {
            "resolved": 1 if result["status"] == "resolved" else 0,
            "failed": 1 if result["status"] == "failed" else 0,
            "already_cached": 0,
            "failures": [result] if result["status"] == "failed" else [],
        }
    else:
        return await resolve_all_missing()


@app.get("/api/album-tracklist-resolver")
async def resolver_status(_=Depends(verify_key)):
    return await get_resolution_status()


# ============================================================
# Ad-hoc query (secured with read-only DB role)
# ============================================================

@app.post("/api/query")
async def adhoc_query(body: dict, _=Depends(verify_key)):
    sql = body.get("sql", "").strip()
    limit = body.get("limit", 100)

    if not sql:
        raise HTTPException(status_code=400, detail="Missing 'sql' field")

    first_word = sql.split()[0].upper() if sql.split() else ""
    if first_word not in ("SELECT", "WITH", "EXPLAIN"):
        raise HTTPException(status_code=400, detail="Only SELECT queries allowed")

    sql_upper = sql.upper()
    if "LIMIT" not in sql_upper:
        sql = sql.rstrip(";") + f" LIMIT {int(limit)}"

    try:
        rows = await fetch_ro(sql)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"results": rows, "count": len(rows)}


# ============================================================
# Chicago shows
# ============================================================

@app.get("/api/chicago-shows")
async def chicago_shows(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    days: Optional[int] = None,
    venue: Optional[str] = None,
    artist: Optional[str] = None,
    status: Optional[str] = Query(default="upcoming", pattern="^(upcoming|sold_out|cancelled|past|all)$"),
    limit: int = Query(default=50, le=1000),
    _=Depends(verify_key),
):
    df = _df(start_date, end_date, days, limit)
    clauses, params = df.build_where(col="show_date")

    # Default to today onward if no date filter specified
    if not df.effective_start and not df.effective_end:
        idx = len(params) + 1
        clauses.append(f"show_date >= ${idx}")
        params.append(date.today())

    if status and status != "all":
        idx = len(params) + 1
        clauses.append(f"status = ${idx}")
        params.append(status)

    if venue:
        idx = len(params) + 1
        clauses.append(f"LOWER(venue_name) %% LOWER(${idx})")
        params.append(venue)

    if artist:
        idx = len(params) + 1
        clauses.append(f"LOWER(artist_name) %% LOWER(${idx})")
        params.append(artist)

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    idx = len(params) + 1

    rows = await fetch(f"""
        SELECT show_id, artist_name, venue_name, show_date, show_time, doors_time,
               support_acts, ticket_url, ticket_price, age_restriction,
               sources, presale_name, presale_start, presale_end, onsale_date,
               status, first_seen, last_verified
        FROM chicago_shows
        {where}
        ORDER BY show_date ASC, show_time ASC NULLS LAST
        LIMIT ${idx}
    """, *params, df.limit)

    return {"shows": rows, "count": len(rows), "filters": {**df.as_dict(), "venue": venue, "artist": artist, "status": status}}


@app.get("/api/chicago-shows/presales")
async def chicago_presales(
    days: Optional[int] = Query(default=14),
    limit: int = Query(default=50, le=1000),
    _=Depends(verify_key),
):
    rows = await fetch("""
        SELECT show_id, artist_name, venue_name, show_date, show_time,
               ticket_url, ticket_price, presale_name, presale_start, presale_end,
               onsale_date, sources, first_seen
        FROM chicago_shows
        WHERE presale_start IS NOT NULL
          AND presale_start > NOW() - INTERVAL '1 day'
          AND presale_start < NOW() + MAKE_INTERVAL(days => $1)
          AND status = 'upcoming'
        ORDER BY presale_start ASC
        LIMIT $2
    """, days, limit)

    return {"presales": rows, "count": len(rows), "filters": {"days": days}}


@app.get("/api/chicago-shows/just-announced")
async def chicago_just_announced(
    days: Optional[int] = Query(default=7),
    limit: int = Query(default=50, le=1000),
    _=Depends(verify_key),
):
    rows = await fetch("""
        SELECT show_id, artist_name, venue_name, show_date, show_time,
               ticket_url, ticket_price, sources, first_seen, status
        FROM chicago_shows
        WHERE first_seen > NOW() - MAKE_INTERVAL(days => $1)
          AND show_date >= CURRENT_DATE
        ORDER BY first_seen DESC
        LIMIT $2
    """, days, limit)

    return {"shows": rows, "count": len(rows), "filters": {"days": days}}


@app.get("/api/chicago-shows/match")
async def chicago_match(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    days: Optional[int] = None,
    min_listens: int = Query(default=1),
    limit: int = Query(default=50, le=1000),
    _=Depends(verify_key),
):
    df = _df(start_date, end_date, days, limit)

    # Build show date filter
    params = []
    clauses = []

    if df.effective_start:
        params.append(df.effective_start.date() if hasattr(df.effective_start, 'date') else df.effective_start)
    else:
        params.append(date.today())
    clauses.append(f"cs.show_date >= ${len(params)}")

    if df.effective_end:
        params.append(df.effective_end.date() if hasattr(df.effective_end, 'date') else df.effective_end)
    else:
        params.append(date.today() + timedelta(days=90))
    clauses.append(f"cs.show_date <= ${len(params)}")

    clauses.append("cs.status = 'upcoming'")

    params.append(min_listens)
    min_idx = len(params)

    params.append(df.limit)
    limit_idx = len(params)

    where = "WHERE " + " AND ".join(clauses)

    # Single query: normalize only the ~3K show rows, then PK join to precomputed stats
    rows = await fetch(f"""
        WITH upcoming AS (
            SELECT cs.show_id, cs.artist_name, cs.venue_name, cs.show_date, cs.show_time,
                   cs.doors_time, cs.support_acts, cs.ticket_url, cs.ticket_price,
                   cs.age_restriction, cs.sources, cs.presale_name, cs.presale_start,
                   cs.presale_end, cs.onsale_date, cs.status, cs.first_seen, cs.last_verified,
                   normalize_artist(cs.artist_name) AS norm_artist
            FROM chicago_shows cs
            {where}
        )
        SELECT u.*,
               als.total_listens, als.unique_tracks, als.unique_albums,
               als.last_listen, als.listens_90d, als.listens_365d, als.top_album
        FROM upcoming u
        JOIN artist_listen_stats als ON als.norm_artist = u.norm_artist
            AND als.norm_artist != ''
        WHERE als.total_listens >= ${min_idx}
        ORDER BY
            LN(als.total_listens + 1)
            * LEAST(als.unique_tracks / 5.0, 3.0)
            * CASE
                WHEN als.listens_90d > 0 THEN 3.0
                WHEN als.listens_365d > 0 THEN 2.0
                ELSE 1.0
              END
            DESC
        LIMIT ${limit_idx}
    """, *params)

    # Format response
    matches = []
    for row in rows:
        total = row["total_listens"]
        tracks = row["unique_tracks"]
        track_factor = min(tracks / 5.0, 3.0)
        recency = 3.0 if row["listens_90d"] > 0 else (2.0 if row["listens_365d"] > 0 else 1.0)
        score = round(math.log(total + 1) * track_factor * recency, 1)

        matches.append({
            "show": {
                k: row[k] for k in [
                    "show_id", "artist_name", "venue_name", "show_date", "show_time",
                    "doors_time", "support_acts", "ticket_url", "ticket_price",
                    "age_restriction", "sources", "presale_name", "presale_start",
                    "presale_end", "onsale_date", "status", "first_seen", "last_verified",
                ]
            },
            "listening_stats": {
                "total_listens": row["total_listens"],
                "unique_tracks": row["unique_tracks"],
                "unique_albums": row["unique_albums"],
                "last_listen": row["last_listen"].isoformat() if row["last_listen"] else None,
                "top_album": row["top_album"],
            },
            "relevance_score": score,
        })

    # Unmatched count
    total_upcoming = await fetchval(f"""
        SELECT COUNT(*) FROM chicago_shows cs {where}
    """, *params[:len(clauses)])

    return {
        "matches": matches,
        "unmatched_count": (total_upcoming or 0) - len(matches),
        "filters": {**df.as_dict(), "min_listens": min_listens},
    }
