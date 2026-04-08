"""
Music Pipeline API — async read-only endpoints backed by asyncpg connection pool.

Usage:
    uvicorn api:app --reload --port 8000
"""

import asyncio
import logging
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Query
from pydantic import BaseModel

from src.database import get_pool, get_ro_pool, close_pools, fetch, fetchrow, fetchval, fetch_ro, execute
from src.auth import verify_key
from src.filters import parse_date_filter, DateFilter
from src.sessions import detect_sessions
from src.musicbrainz import resolve_single, resolve_all_missing, get_resolution_status
from src.schema_registry import SCHEMA

logger = logging.getLogger(__name__)

app = FastAPI(title="Music Listening API", version="0.2.0")


@app.on_event("startup")
async def startup():
    await get_pool()


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


async def _get_album_track_count(artist: str, album: str) -> tuple[int, str]:
    """Get track count from album_tracklist or fall back to heuristic.

    Returns (track_count, source) where source is 'musicbrainz' or 'heuristic'.
    """
    row = await fetchrow(
        "SELECT track_count FROM album_tracklist WHERE LOWER(raw_artist) = LOWER($1) AND LOWER(raw_album) = LOWER($2)",
        artist, album,
    )
    if row:
        return row["track_count"], "musicbrainz"

    count = await fetchval(
        "SELECT COUNT(DISTINCT raw_title) FROM listen_events WHERE LOWER(raw_artist) = LOWER($1) AND LOWER(raw_album) = LOWER($2)",
        artist, album,
    )
    return count or 0, "heuristic"


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
        total_tracks, source = await _get_album_track_count(album["raw_artist"], album["raw_album"])
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
# Album sessions
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
    gap_minutes: int = Query(default=30),
    limit: int = Query(default=50, le=1000),
    _=Depends(verify_key),
):
    df = _df(start_date, end_date, days, limit)
    clauses, params = df.build_where(col="le.listened_at")
    clauses.append("le.raw_album IS NOT NULL")

    if artist:
        idx = len(params) + 1
        clauses.append(f"LOWER(le.raw_artist) = LOWER(${idx})")
        params.append(artist)
    if album:
        idx = len(params) + 1
        clauses.append(f"LOWER(le.raw_album) = LOWER(${idx})")
        params.append(album)

    where = "WHERE " + " AND ".join(clauses)

    # Get distinct artist+album pairs in the filtered range
    album_pairs = await fetch(f"""
        SELECT DISTINCT le.raw_artist, le.raw_album
        FROM listen_events le
        {where}
        ORDER BY le.raw_artist, le.raw_album
    """, *params)

    all_sessions = []

    for pair in album_pairs:
        a_artist = pair["raw_artist"]
        a_album = pair["raw_album"]

        # Get all listens for this album pair (within date range)
        listen_clauses, listen_params = df.build_where()
        listen_clauses.append("LOWER(raw_artist) = LOWER($" + str(len(listen_params) + 1) + ")")
        listen_params.append(a_artist)
        listen_clauses.append("LOWER(raw_album) = LOWER($" + str(len(listen_params) + 1) + ")")
        listen_params.append(a_album)

        listen_where = "WHERE " + " AND ".join(listen_clauses)

        listens = await fetch(f"""
            SELECT raw_title, listened_at
            FROM listen_events
            {listen_where}
            ORDER BY listened_at ASC
        """, *listen_params)

        if not listens:
            continue

        total_tracks, source = await _get_album_track_count(a_artist, a_album)
        if total_tracks == 0:
            continue

        sessions = detect_sessions(listens, total_tracks, gap_minutes)

        for s in sessions:
            if min_completion is not None and s["completion"] < min_completion:
                continue
            if session_type and s["session_type"] != session_type:
                continue

            all_sessions.append({
                "raw_artist": a_artist,
                "raw_album": a_album,
                **s,
                "tracklist_source": source,
            })

    # Sort by session_start descending, then limit
    all_sessions.sort(key=lambda s: s["session_start"], reverse=True)
    all_sessions = all_sessions[:df.limit]

    return {
        "sessions": all_sessions,
        "filters": {
            **df.as_dict(),
            "artist": artist,
            "album": album,
            "min_completion": min_completion,
            "session_type": session_type,
            "gap_minutes": gap_minutes,
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

        album_results = []
        for alb in albums:
            total_tracks, _ = await _get_album_track_count(artist_name, alb["raw_album"])
            completion = round(alb["tracks_heard"] / total_tracks, 2) if total_tracks > 0 else 0.0

            # Get session counts for this album
            listens = await fetch("""
                SELECT raw_title, listened_at
                FROM listen_events
                WHERE LOWER(raw_artist) = LOWER($1) AND LOWER(raw_album) = LOWER($2)
                ORDER BY listened_at ASC
            """, artist_name, alb["raw_album"])

            sessions = detect_sessions(listens, total_tracks) if total_tracks > 0 else []
            full_sessions = sum(1 for s in sessions if s["session_type"] == "full")
            partial_sessions = sum(1 for s in sessions if s["session_type"] == "partial")

            album_results.append({
                "raw_album": alb["raw_album"],
                "tracks_heard": alb["tracks_heard"],
                "total_tracks": total_tracks,
                "completion": completion,
                "listen_count": alb["listen_count"],
                "full_sessions": full_sessions,
                "partial_sessions": partial_sessions,
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
