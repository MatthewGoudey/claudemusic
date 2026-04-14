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
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from src.database import get_pool, get_ro_pool, close_pools, fetch, fetchrow, fetchval, fetch_ro, execute
from src.auth import verify_key
from src.filters import parse_date_filter, DateFilter
from src.musicbrainz import resolve_single, resolve_all_missing, get_resolution_status, audit_and_reresolve
from src.lastfm import resolve_missing_via_lastfm
from src.schema_registry import SCHEMA
from src.modes import MODES
from src.genres import CANONICAL_GENRES, CANONICAL_GENRE_SET, CANONICAL_GENRE_LOWER
from src.formatters import (
    compact_top_artists, compact_top_tracks, compact_top_albums,
    compact_recent, compact_album_completion, compact_chicago_shows_match,
    compact_discover, compact_chicago_shows, compact_canonical,
    compact_canonical_gaps, _fmt_ts,
)
import math

logger = logging.getLogger(__name__)

app = FastAPI(title="Music Listening API", version="0.2.0")


@app.on_event("startup")
async def startup():
    pool = await get_pool()
    # Ensure chicago_shows table exists
    async with pool.acquire() as conn:
        exists = await conn.fetchval("SELECT to_regclass('public.chicago_shows')")
        import os
        if exists is None:
            sql_path = os.path.join(os.path.dirname(__file__), "sql", "chicago_shows.sql")
            with open(sql_path) as f:
                await conn.execute(f.read())
            logger.info("Created chicago_shows table")
        # Add festival_name column if missing
        await conn.execute("""
            ALTER TABLE chicago_shows ADD COLUMN IF NOT EXISTS festival_name TEXT
        """)
        # Ensure venues table exists
        venues_sql = os.path.join(os.path.dirname(__file__), "sql", "venues.sql")
        if os.path.exists(venues_sql):
            with open(venues_sql) as f:
                await conn.execute(f.read())
        # Ensure show_interests table exists
        interests_sql = os.path.join(os.path.dirname(__file__), "sql", "show_interests.sql")
        if os.path.exists(interests_sql):
            with open(interests_sql) as f:
                await conn.execute(f.read())
        # Ensure canonical_albums table exists
        canonical_sql = os.path.join(os.path.dirname(__file__), "sql", "canonical_albums.sql")
        if os.path.exists(canonical_sql):
            with open(canonical_sql) as f:
                await conn.execute(f.read())
        # Ensure canonical_listen_matches table exists
        clm_sql = os.path.join(os.path.dirname(__file__), "sql", "canonical_listen_matches.sql")
        if os.path.exists(clm_sql):
            with open(clm_sql) as f:
                await conn.execute(f.read())
        # Ensure album_tracklist has quality tracking columns
        await conn.execute("""
            ALTER TABLE album_tracklist ADD COLUMN IF NOT EXISTS release_type TEXT NOT NULL DEFAULT 'unknown';
            ALTER TABLE album_tracklist ADD COLUMN IF NOT EXISTS quality_flag TEXT;
            ALTER TABLE album_tracklist ADD COLUMN IF NOT EXISTS resolution_notes TEXT;
        """)
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
# Modes (no auth)
# ============================================================

@app.get("/api/modes")
async def modes_index():
    return {
        "modes": [
            {"id": mode_id, "name": m["name"], "description": m["description"]}
            for mode_id, m in MODES.items()
        ]
    }


@app.get("/api/modes/{mode_id}", response_class=PlainTextResponse)
async def mode_detail(mode_id: str):
    if mode_id not in MODES:
        valid = ", ".join(MODES.keys())
        return PlainTextResponse(
            f"Unknown mode: {mode_id}\nValid modes: {valid}",
            status_code=404,
        )
    return PlainTextResponse(MODES[mode_id]["spec"])


# ============================================================
# Session start (combined startup endpoint)
# ============================================================

def _suggest_mode(recent_rows, top_recent, top_alltime):
    """Analyze listening patterns and suggest an exploration mode.

    Returns (mode_id, reason).
    """
    if not recent_rows:
        return "finish", "No recent listening data — finish mode always has candidates."

    # Count plays per artist in recent data
    artist_plays = {}
    for r in recent_rows:
        a = r.get("raw_artist", "")
        artist_plays[a] = artist_plays.get(a, 0) + 1
    total_recent = sum(artist_plays.values())

    if total_recent == 0:
        return "finish", "No recent listening data."

    # Rule 3: One artist dominates (>30% of recent plays)
    top_artist = max(artist_plays, key=artist_plays.get)
    top_pct = artist_plays[top_artist] / total_recent
    if top_pct > 0.30:
        return "disco", f"{top_artist} is {round(top_pct * 100)}% of recent plays — explore their deeper catalog."

    # Rule 2: Very scattered (no artist > 10% of recent)
    max_pct = max(v / total_recent for v in artist_plays.values())
    if max_pct < 0.10 and len(artist_plays) > 8:
        return "finish", "Scattered listening across many artists — good time to complete something unfinished."

    # Rule 5: New discovery — artist in recent top 10 not in alltime top 10
    alltime_names = {r.get("raw_artist", "").lower() for r in (top_alltime or [])[:10]}
    recent_top = sorted(artist_plays.items(), key=lambda x: -x[1])[:10]
    for name, _ in recent_top:
        if name.lower() not in alltime_names and name:
            return "roots", f"You're exploring {name} — trace the genre roots of what drew you in."

    # Rule 4: Low volume
    if total_recent < 20:
        return "mood", "Light listening lately — mood-based discovery matches low-energy exploration."

    # Rule 6: Default
    return "finish", "Finish mode always has candidates ready."


def _build_listening_state(recent_rows, top_recent):
    """Build a 1-2 sentence natural language summary of listening state."""
    if not recent_rows or not top_recent:
        return "No recent listening data available."

    # Count artist plays in recent
    artist_plays = {}
    for r in recent_rows:
        a = r.get("raw_artist", "")
        artist_plays[a] = artist_plays.get(a, 0) + 1

    top3 = sorted(artist_plays.items(), key=lambda x: -x[1])[:3]
    top3_str = ", ".join(f"{name} ({count})" for name, count in top3)

    unique_artists = len(artist_plays)
    total = sum(artist_plays.values())

    # Concentration assessment
    if top3 and top3[0][1] / max(total, 1) > 0.30:
        concentration = f"Concentrated on {top3[0][0]}."
    elif unique_artists > 15:
        concentration = "Diverse listening across many artists."
    else:
        concentration = "Moderate variety."

    return f"{concentration} Top recent: {top3_str}. {unique_artists} unique artists in window."


@app.get("/api/session-start")
async def session_start(
    format: str = Query(default="compact", pattern="^(compact|full)$"),
    _=Depends(verify_key),
):
    # Run all queries concurrently
    recent_task = fetch("""
        SELECT raw_title, raw_artist, raw_album, listened_at
        FROM listen_events ORDER BY listened_at DESC LIMIT 50
    """)
    top_recent_task = fetch("""
        SELECT raw_artist, COUNT(*) AS listen_count,
               COUNT(DISTINCT raw_title) AS unique_tracks
        FROM listen_events
        WHERE listened_at >= NOW() - INTERVAL '30 days'
        GROUP BY raw_artist ORDER BY listen_count DESC LIMIT 10
    """)
    top_alltime_task = fetch("""
        SELECT raw_artist, COUNT(*) AS listen_count,
               COUNT(DISTINCT raw_title) AS unique_tracks
        FROM listen_events
        GROUP BY raw_artist ORDER BY listen_count DESC LIMIT 10
    """)
    summary_task = fetchrow("""
        SELECT COUNT(*) AS total_listens,
               COUNT(DISTINCT raw_artist) AS unique_artists
        FROM listen_events
    """)
    interests_task = fetch("""
        SELECT cs.show_id, cs.artist_name, cs.venue_name, cs.show_date,
               si.status AS interest_status, si.note
        FROM show_interests si
        JOIN chicago_shows cs ON cs.show_id = si.show_id
        WHERE cs.show_date >= CURRENT_DATE
        ORDER BY cs.show_date ASC LIMIT 20
    """)

    recent_rows, top_recent, top_alltime, summary_row, interests = await asyncio.gather(
        recent_task, top_recent_task, top_alltime_task, summary_task, interests_task
    )

    mode_id, mode_reason = _suggest_mode(recent_rows, top_recent, top_alltime)

    if format == "full":
        return {
            "recent": recent_rows[:20],
            "top_artists_recent": top_recent,
            "top_artists_alltime": top_alltime,
            "summary": summary_row or {},
            "tracked_shows": interests,
            "suggested_mode": {"id": mode_id, "reason": mode_reason},
        }

    # Compact format
    listening_state = _build_listening_state(recent_rows, top_recent)
    recent_summary_lines = []
    for r in recent_rows[:20]:
        a = r.get("raw_artist", "?")
        t = r.get("raw_title", "?")
        alb = r.get("raw_album", "")
        alb_part = f" ({alb})" if alb else ""
        ts = r.get("listened_at")
        ts_str = _fmt_ts(ts) if ts else ""
        recent_summary_lines.append(f"{ts_str} {a} - {t}{alb_part}")
    recent_text = "\n".join(recent_summary_lines)

    top_recent_str = compact_top_artists(top_recent)
    top_alltime_str = compact_top_artists(top_alltime)

    tracked = []
    for row in interests:
        tracked.append({
            "show_id": row["show_id"],
            "artist": row["artist_name"],
            "venue": row["venue_name"],
            "date": row["show_date"].isoformat() if row["show_date"] else None,
            "status": row["interest_status"],
            "note": row.get("note"),
        })

    return {
        "listening_state": listening_state,
        "suggested_mode": {"id": mode_id, "reason": mode_reason},
        "recent_summary": recent_text,
        "top_artists_recent": top_recent_str,
        "top_artists_alltime": top_alltime_str,
        "tracked_shows": tracked,
        "total_listens": summary_row["total_listens"] if summary_row else 0,
        "unique_artists": summary_row["unique_artists"] if summary_row else 0,
    }


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
    limit: int = Query(default=10, le=1000),
    format: str = Query(default="compact", pattern="^(compact|json)$"),
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
    if format == "compact":
        return PlainTextResponse(compact_top_artists(rows))
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
    limit: int = Query(default=10, le=1000),
    format: str = Query(default="compact", pattern="^(compact|json)$"),
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
    if format == "compact":
        return PlainTextResponse(compact_top_tracks(rows))
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
    limit: int = Query(default=10, le=1000),
    format: str = Query(default="compact", pattern="^(compact|json)$"),
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
    if format == "compact":
        return PlainTextResponse(compact_top_albums(rows))
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
    limit: int = Query(default=20, le=500),
    format: str = Query(default="compact", pattern="^(compact|json)$"),
    _=Depends(verify_key),
):
    rows = await fetch("""
        SELECT raw_title, raw_artist, raw_album, platform, source_app,
               listened_at, content_type, confidence
        FROM listen_events
        ORDER BY listened_at DESC
        LIMIT $1
    """, limit)
    if format == "compact":
        return PlainTextResponse(compact_recent(rows))
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
    limit: int = Query(default=10, le=1000),
    format: str = Query(default="compact", pattern="^(compact|json)$"),
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

    if format == "compact":
        return PlainTextResponse(compact_album_completion(results))
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


class ManualOverrideRequest(BaseModel):
    artist: str
    album: str
    track_count: int
    release_type: str = "Album"
    notes: str = ""


@app.post("/api/album-tracklist-override")
async def manual_tracklist_override(body: ManualOverrideRequest, _=Depends(verify_key)):
    """Manually set or override an album's track count and release type."""
    from datetime import datetime, timezone
    await execute(
        """INSERT INTO album_tracklist (raw_artist, raw_album, track_count, release_type, source, resolved_at, resolution_notes)
           VALUES ($1, $2, $3, $4, 'manual', $5, $6)
           ON CONFLICT (raw_artist, raw_album)
           DO UPDATE SET track_count = $3, release_type = $4, source = 'manual',
                         resolved_at = $5, resolution_notes = $6, quality_flag = 'manual_verified'""",
        body.artist, body.album, body.track_count, body.release_type,
        datetime.now(timezone.utc), body.notes,
    )
    await execute(
        "DELETE FROM album_sessions WHERE LOWER(raw_artist) = LOWER($1) AND LOWER(raw_album) = LOWER($2)",
        body.artist, body.album,
    )
    return {"status": "ok", "artist": body.artist, "album": body.album, "track_count": body.track_count}


@app.post("/api/album-tracklist-audit")
async def trigger_audit(_=Depends(verify_key)):
    """Re-resolve albums with quality issues (tracks_played > track_count, box sets)."""
    return await audit_and_reresolve()


@app.post("/api/album-tracklist-lastfm")
async def trigger_lastfm_resolver(body: dict = {}, _=Depends(verify_key)):
    """Resolve unresolved albums using Last.fm as fallback source."""
    limit = body.get("limit", 500) if body else 500
    return await resolve_missing_via_lastfm(limit=limit)


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
    genre: Optional[str] = None,
    festival: Optional[str] = None,
    status: Optional[str] = Query(default="upcoming", pattern="^(upcoming|sold_out|cancelled|past|all)$"),
    limit: int = Query(default=20, le=1000),
    format: str = Query(default="compact", pattern="^(compact|json)$"),
    _=Depends(verify_key),
):
    df = _df(start_date, end_date, days, limit)
    clauses, params = df.build_where(col="cs.show_date")

    # Default to today onward if no date filter specified
    if not df.effective_start and not df.effective_end:
        idx = len(params) + 1
        clauses.append(f"cs.show_date >= ${idx}")
        params.append(date.today())

    if status and status != "all":
        idx = len(params) + 1
        clauses.append(f"cs.status = ${idx}")
        params.append(status)

    if venue:
        idx = len(params) + 1
        clauses.append(f"LOWER(cs.venue_name) %% LOWER(${idx})")
        params.append(venue)

    if artist:
        idx = len(params) + 1
        clauses.append(f"LOWER(cs.artist_name) %% LOWER(${idx})")
        params.append(artist)

    if festival:
        idx = len(params) + 1
        clauses.append(f"LOWER(cs.festival_name) LIKE LOWER(${idx})")
        params.append(f"%{festival}%")

    genre_join = ""
    if genre:
        idx = len(params) + 1
        genre_join = "JOIN artist_tags at ON at.norm_artist = normalize_artist(cs.artist_name)"
        clauses.append(f"LOWER(at.tag) LIKE LOWER(${idx})")
        params.append(f"%{genre}%")

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    idx = len(params) + 1

    rows = await fetch(f"""
        SELECT DISTINCT cs.show_id, cs.artist_name, cs.venue_name, cs.show_date, cs.show_time,
               cs.doors_time, cs.support_acts, cs.ticket_url, cs.ticket_price, cs.age_restriction,
               cs.sources, cs.presale_name, cs.presale_start, cs.presale_end, cs.onsale_date,
               cs.festival_name, cs.status, cs.first_seen, cs.last_verified,
               v.travel_driving_min, v.travel_transit_min, v.travel_best_min,
               si.status AS interest_status, si.note AS interest_note
        FROM chicago_shows cs
        LEFT JOIN venues v ON LOWER(v.venue_name) = LOWER(cs.venue_name)
        LEFT JOIN show_interests si ON si.show_id = cs.show_id
        {genre_join}
        {where}
        ORDER BY cs.show_date ASC, cs.show_time ASC NULLS LAST
        LIMIT ${idx}
    """, *params, df.limit)

    shows = []
    for row in rows:
        show = dict(row)
        show["travel"] = {
            "driving_min": show.pop("travel_driving_min", None),
            "transit_min": show.pop("travel_transit_min", None),
            "best_min": show.pop("travel_best_min", None),
        }
        shows.append(show)

    if format == "compact":
        return PlainTextResponse(compact_chicago_shows(shows))
    return {"shows": shows, "count": len(shows), "filters": {**df.as_dict(), "venue": venue, "artist": artist, "genre": genre, "festival": festival, "status": status}}


@app.get("/api/chicago-shows/presales")
async def chicago_presales(
    days: Optional[int] = Query(default=14),
    limit: int = Query(default=50, le=1000),
    _=Depends(verify_key),
):
    rows = await fetch("""
        SELECT cs.show_id, cs.artist_name, cs.venue_name, cs.show_date, cs.show_time,
               cs.ticket_url, cs.ticket_price, cs.presale_name, cs.presale_start, cs.presale_end,
               cs.onsale_date, cs.sources, cs.festival_name, cs.first_seen,
               v.travel_driving_min, v.travel_transit_min, v.travel_best_min,
               si.status AS interest_status, si.note AS interest_note
        FROM chicago_shows cs
        LEFT JOIN venues v ON LOWER(v.venue_name) = LOWER(cs.venue_name)
        LEFT JOIN show_interests si ON si.show_id = cs.show_id
        WHERE cs.presale_start IS NOT NULL
          AND cs.presale_start > NOW() - INTERVAL '1 day'
          AND cs.presale_start < NOW() + MAKE_INTERVAL(days => $1)
          AND cs.status = 'upcoming'
        ORDER BY cs.presale_start ASC
        LIMIT $2
    """, days, limit)

    presales = []
    for row in rows:
        show = dict(row)
        show["travel"] = {
            "driving_min": show.pop("travel_driving_min", None),
            "transit_min": show.pop("travel_transit_min", None),
            "best_min": show.pop("travel_best_min", None),
        }
        presales.append(show)

    return {"presales": presales, "count": len(presales), "filters": {"days": days}}


@app.get("/api/chicago-shows/just-announced")
async def chicago_just_announced(
    days: Optional[int] = Query(default=7),
    limit: int = Query(default=50, le=1000),
    _=Depends(verify_key),
):
    rows = await fetch("""
        SELECT cs.show_id, cs.artist_name, cs.venue_name, cs.show_date, cs.show_time,
               cs.ticket_url, cs.ticket_price, cs.sources, cs.festival_name, cs.first_seen, cs.status,
               v.travel_driving_min, v.travel_transit_min, v.travel_best_min,
               si.status AS interest_status, si.note AS interest_note
        FROM chicago_shows cs
        LEFT JOIN venues v ON LOWER(v.venue_name) = LOWER(cs.venue_name)
        LEFT JOIN show_interests si ON si.show_id = cs.show_id
        WHERE cs.first_seen > NOW() - MAKE_INTERVAL(days => $1)
          AND cs.show_date >= CURRENT_DATE
        ORDER BY cs.first_seen DESC
        LIMIT $2
    """, days, limit)

    shows = []
    for row in rows:
        show = dict(row)
        show["travel"] = {
            "driving_min": show.pop("travel_driving_min", None),
            "transit_min": show.pop("travel_transit_min", None),
            "best_min": show.pop("travel_best_min", None),
        }
        shows.append(show)

    return {"shows": shows, "count": len(shows), "filters": {"days": days}}


@app.get("/api/chicago-shows/match")
async def chicago_match(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    days: Optional[int] = None,
    min_listens: int = Query(default=1),
    genre: Optional[str] = None,
    festival: Optional[str] = None,
    limit: int = Query(default=15, le=1000),
    format: str = Query(default="compact", pattern="^(compact|json)$"),
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

    genre_join = ""
    if genre:
        params.append(f"%{genre}%")
        genre_join = f"JOIN artist_tags gt ON gt.norm_artist = normalize_artist(cs.artist_name) AND LOWER(gt.tag) LIKE LOWER(${len(params)})"

    if festival:
        params.append(f"%{festival}%")
        clauses.append(f"LOWER(cs.festival_name) LIKE LOWER(${len(params)})")

    # Track params needed for the WHERE clause (for unmatched count query)
    where_param_count = len(params)

    params.append(min_listens)
    min_idx = len(params)

    params.append(df.limit)
    limit_idx = len(params)

    where = "WHERE " + " AND ".join(clauses)

    # Single query: normalize show rows, join to stats + venue travel data
    rows = await fetch(f"""
        WITH upcoming AS (
            SELECT DISTINCT cs.show_id, cs.artist_name, cs.venue_name, cs.show_date, cs.show_time,
                   cs.doors_time, cs.support_acts, cs.ticket_url, cs.ticket_price,
                   cs.age_restriction, cs.sources, cs.presale_name, cs.presale_start,
                   cs.presale_end, cs.onsale_date, cs.festival_name,
                   cs.status, cs.first_seen, cs.last_verified,
                   normalize_artist(cs.artist_name) AS norm_artist
            FROM chicago_shows cs
            {genre_join}
            {where}
        ),
        travel_range AS (
            SELECT COALESCE(MIN(travel_best_min), 0) AS tmin,
                   COALESCE(MAX(travel_best_min), 60) AS tmax
            FROM venues
        )
        SELECT u.*,
               als.total_listens, als.unique_tracks, als.unique_albums,
               als.last_listen, als.listens_90d, als.listens_365d, als.top_album,
               v.travel_driving_min, v.travel_transit_min, v.travel_best_min,
               tr.tmin AS travel_min_all, tr.tmax AS travel_max_all,
               si.status AS interest_status, si.note AS interest_note
        FROM upcoming u
        JOIN artist_listen_stats als ON als.norm_artist = u.norm_artist
            AND als.norm_artist != ''
        LEFT JOIN venues v ON LOWER(v.venue_name) = LOWER(u.venue_name)
        LEFT JOIN show_interests si ON si.show_id = u.show_id
        CROSS JOIN travel_range tr
        WHERE als.total_listens >= ${min_idx}
        ORDER BY
            LN(als.total_listens + 1)
            * LEAST(als.unique_tracks / 5.0, 3.0)
            * CASE
                WHEN als.listens_90d > 0 THEN 3.0
                WHEN als.listens_365d > 0 THEN 2.0
                ELSE 1.0
              END
            * (1.0 - 0.3 * (COALESCE(v.travel_best_min, tr.tmax) - tr.tmin)::FLOAT
                           / GREATEST(tr.tmax - tr.tmin, 1))
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
        tmin = row.get("travel_min_all") or 0
        tmax = row.get("travel_max_all") or 60
        travel_min = row.get("travel_best_min")
        travel_factor = 1.0 - 0.3 * ((travel_min if travel_min is not None else tmax) - tmin) / max(tmax - tmin, 1)
        score = round(math.log(total + 1) * track_factor * recency * travel_factor, 1)

        matches.append({
            "show": {
                k: row[k] for k in [
                    "show_id", "artist_name", "venue_name", "show_date", "show_time",
                    "doors_time", "support_acts", "ticket_url", "ticket_price",
                    "age_restriction", "sources", "presale_name", "presale_start",
                    "presale_end", "onsale_date", "festival_name",
                    "status", "first_seen", "last_verified",
                ]
            },
            "travel": {
                "driving_min": row.get("travel_driving_min"),
                "transit_min": row.get("travel_transit_min"),
                "best_min": row.get("travel_best_min"),
            },
            "listening_stats": {
                "total_listens": row["total_listens"],
                "unique_tracks": row["unique_tracks"],
                "unique_albums": row["unique_albums"],
                "last_listen": row["last_listen"].isoformat() if row["last_listen"] else None,
                "top_album": row["top_album"],
            },
            "interest_status": row.get("interest_status"),
            "interest_note": row.get("interest_note"),
            "relevance_score": score,
        })

    # Unmatched count — needs all WHERE params (dates + optional festival)
    total_upcoming = await fetchval(f"""
        SELECT COUNT(*) FROM chicago_shows cs {where}
    """, *params[:where_param_count])

    if format == "compact":
        return PlainTextResponse(compact_chicago_shows_match(matches))
    return {
        "matches": matches,
        "unmatched_count": (total_upcoming or 0) - len(matches),
        "filters": {**df.as_dict(), "min_listens": min_listens, "genre": genre, "festival": festival},
    }


# ============================================================
# Show interest tracking
# ============================================================


class InterestRequest(BaseModel):
    show_id: int
    status: str  # going, interested, not_interested, cant_afford
    note: Optional[str] = None


@app.put("/api/chicago-shows/interest")
async def set_interest(req: InterestRequest, _=Depends(verify_key)):
    valid = ("going", "interested", "not_interested", "cant_afford")
    if req.status not in valid:
        raise HTTPException(400, f"status must be one of: {', '.join(valid)}")

    row = await fetchrow("""
        INSERT INTO show_interests (show_id, status, note)
        VALUES ($1, $2, $3)
        ON CONFLICT (show_id) DO UPDATE SET
            status = EXCLUDED.status,
            note = EXCLUDED.note,
            updated_at = NOW()
        RETURNING interest_id, show_id, status, note, created_at, updated_at
    """, req.show_id, req.status, req.note)

    if not row:
        raise HTTPException(404, "show_id not found")

    return row


@app.delete("/api/chicago-shows/interest/{show_id}")
async def delete_interest(show_id: int, _=Depends(verify_key)):
    result = await execute("""
        DELETE FROM show_interests WHERE show_id = $1
    """, show_id)

    if result == "DELETE 0":
        raise HTTPException(404, "No interest tracked for this show")

    return {"deleted": True, "show_id": show_id}


@app.get("/api/chicago-shows/interest")
async def list_interests(
    status: Optional[str] = None,
    limit: int = Query(default=50, le=1000),
    _=Depends(verify_key),
):
    clauses = ["cs.show_date >= CURRENT_DATE"]
    params = []

    if status:
        params.append(status)
        clauses.append(f"si.status = ${len(params)}")

    where = "WHERE " + " AND ".join(clauses)
    params.append(limit)
    limit_idx = len(params)

    rows = await fetch(f"""
        SELECT cs.show_id, cs.artist_name, cs.venue_name, cs.show_date, cs.show_time,
               cs.doors_time, cs.ticket_url, cs.ticket_price, cs.festival_name,
               cs.status AS show_status,
               si.status AS interest_status, si.note, si.created_at AS tracked_at, si.updated_at,
               v.travel_driving_min, v.travel_transit_min, v.travel_best_min
        FROM show_interests si
        JOIN chicago_shows cs ON cs.show_id = si.show_id
        LEFT JOIN venues v ON LOWER(v.venue_name) = LOWER(cs.venue_name)
        {where}
        ORDER BY cs.show_date ASC
        LIMIT ${limit_idx}
    """, *params)

    results = []
    for row in rows:
        r = dict(row)
        r["travel"] = {
            "driving_min": r.pop("travel_driving_min", None),
            "transit_min": r.pop("travel_transit_min", None),
            "best_min": r.pop("travel_best_min", None),
        }
        results.append(r)

    return {"interests": results, "count": len(results), "filters": {"status": status}}


# ============================================================
# Discover: similar artists with events
# ============================================================

@app.get("/api/discover")
async def discover(
    seed: str = Query(..., description="Seed artist name"),
    include_events: bool = Query(default=True),
    max_similar: int = Query(default=15, le=100),
    exclude_heard: bool = Query(default=False),
    genre: Optional[str] = None,
    format: str = Query(default="compact", pattern="^(compact|json)$"),
    _=Depends(verify_key),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Normalize seed
        norm_seed = await conn.fetchval("SELECT normalize_artist($1)", seed)
        if not norm_seed or not norm_seed.strip():
            raise HTTPException(status_code=400, detail=f"Could not normalize artist name: {seed}")

        # Seed artist stats
        seed_stats = await conn.fetchrow("""
            SELECT total_listens, unique_tracks, unique_albums, last_listen, top_album
            FROM artist_listen_stats WHERE norm_artist = $1
        """, norm_seed)

        # Similar artists with listen stats and tags
        similar_rows = await conn.fetch("""
            SELECT
                s.norm_artist_b,
                s.score AS similarity_score,
                s.source,
                als.raw_artist,
                als.total_listens,
                als.unique_tracks,
                als.last_listen
            FROM artist_similarity s
            LEFT JOIN artist_listen_stats als ON als.norm_artist = s.norm_artist_b
            WHERE s.norm_artist_a = $1
            ORDER BY s.score DESC
            LIMIT $2
        """, norm_seed, max_similar)

        # Build result list
        similar_norms = []
        similar_artists = []
        for row in similar_rows:
            listen_count = row["total_listens"] or 0
            heard = listen_count > 0

            if exclude_heard and heard:
                continue

            norm_b = row["norm_artist_b"]
            similar_norms.append(norm_b)

            # Use raw_artist from stats if available, otherwise titlecase the norm
            display_name = row["raw_artist"] or norm_b.title()

            similar_artists.append({
                "name": display_name,
                "norm_artist": norm_b,
                "similarity_score": row["similarity_score"],
                "source": row["source"],
                "heard_before": heard,
                "listen_count": listen_count,
                "tags": [],
                "upcoming_shows": [],
            })

        # Fetch tags for all similar artists in one query
        if similar_norms:
            tag_rows = await conn.fetch("""
                SELECT norm_artist, tag, weight
                FROM artist_tags
                WHERE norm_artist = ANY($1)
                ORDER BY norm_artist, weight DESC
            """, similar_norms)

            tag_map: dict[str, list[str]] = {}
            for tr in tag_rows:
                tag_map.setdefault(tr["norm_artist"], []).append(tr["tag"])

            for artist in similar_artists:
                artist["tags"] = tag_map.get(artist["norm_artist"], [])

            # Filter by genre if specified
            if genre:
                genre_lower = genre.lower()
                similar_artists = [
                    a for a in similar_artists
                    if any(genre_lower in t for t in a["tags"])
                ]
                similar_norms = [a["norm_artist"] for a in similar_artists]

        # Fetch upcoming shows for similar artists + seed
        if include_events and similar_norms:
            all_norms = [norm_seed] + similar_norms
            show_rows = await conn.fetch("""
                SELECT
                    normalize_artist(cs.artist_name) AS norm_artist,
                    cs.show_id, cs.artist_name, cs.venue_name, cs.show_date,
                    cs.show_time, cs.ticket_url, cs.festival_name, cs.status,
                    v.travel_best_min, v.travel_driving_min, v.travel_transit_min
                FROM chicago_shows cs
                LEFT JOIN venues v ON LOWER(v.venue_name) = LOWER(cs.venue_name)
                WHERE normalize_artist(cs.artist_name) = ANY($1)
                  AND cs.show_date >= CURRENT_DATE
                  AND cs.status = 'upcoming'
                ORDER BY cs.show_date ASC
            """, all_norms)

            show_map: dict[str, list[dict]] = {}
            for sr in show_rows:
                show_map.setdefault(sr["norm_artist"], []).append({
                    "show_id": sr["show_id"],
                    "artist_name": sr["artist_name"],
                    "venue_name": sr["venue_name"],
                    "date": sr["show_date"].isoformat() if sr["show_date"] else None,
                    "time": sr["show_time"].isoformat() if sr["show_time"] else None,
                    "ticket_url": sr["ticket_url"],
                    "festival_name": sr["festival_name"],
                    "travel": {
                        "driving_min": sr["travel_driving_min"],
                        "transit_min": sr["travel_transit_min"],
                        "best_min": sr["travel_best_min"],
                    },
                })

            for artist in similar_artists:
                artist["upcoming_shows"] = show_map.get(artist["norm_artist"], [])

            seed_shows = show_map.get(norm_seed, [])
        else:
            seed_shows = []

        # Clean norm_artist from output
        for artist in similar_artists:
            del artist["norm_artist"]

    result = {
        "seed_artist": seed,
        "seed_norm": norm_seed,
        "seed_stats": {
            "total_listens": seed_stats["total_listens"] if seed_stats else 0,
            "unique_tracks": seed_stats["unique_tracks"] if seed_stats else 0,
            "unique_albums": seed_stats["unique_albums"] if seed_stats else 0,
            "last_listen": seed_stats["last_listen"].isoformat() if seed_stats and seed_stats["last_listen"] else None,
            "top_album": seed_stats["top_album"] if seed_stats else None,
        },
        "seed_upcoming_shows": seed_shows,
        "similar_artists": similar_artists,
        "filters": {
            "max_similar": max_similar,
            "exclude_heard": exclude_heard,
            "include_events": include_events,
            "genre": genre,
        },
    }
    if format == "compact":
        return PlainTextResponse(compact_discover(result))
    return result


# ============================================================
# Canonical albums
# ============================================================


class CanonicalAlbumEntry(BaseModel):
    artist: str
    album: str
    year: Optional[int] = None
    genre: str
    subgenre: Optional[str] = None
    tier: str = "essential"
    description: Optional[str] = None


class CanonicalBatchRequest(BaseModel):
    albums: list[CanonicalAlbumEntry]


@app.post("/api/canonical")
async def upsert_canonical(body: CanonicalBatchRequest, _=Depends(verify_key)):
    valid_tiers = ("essential", "important", "deep")
    bad_genres = []
    for entry in body.albums:
        if entry.tier not in valid_tiers:
            raise HTTPException(400, f"tier must be one of: {', '.join(valid_tiers)}")
        # Auto-correct casing, reject unknown genres
        if entry.genre in CANONICAL_GENRE_SET:
            pass  # exact match
        elif entry.genre.lower() in CANONICAL_GENRE_LOWER:
            entry.genre = CANONICAL_GENRE_LOWER[entry.genre.lower()]
        else:
            bad_genres.append(entry.genre)
    if bad_genres:
        raise HTTPException(400, f"Unknown genre(s): {', '.join(set(bad_genres))}. "
                            f"Use GET /api/canonical/genres for the full list.")

    count = 0
    for entry in body.albums:
        await execute("""
            INSERT INTO canonical_albums (artist, album, year, genre, subgenre, tier, description)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (artist, album) DO UPDATE SET
                year = EXCLUDED.year,
                genre = EXCLUDED.genre,
                subgenre = EXCLUDED.subgenre,
                tier = EXCLUDED.tier,
                description = EXCLUDED.description
        """, entry.artist, entry.album, entry.year, entry.genre,
            entry.subgenre, entry.tier, entry.description)
        count += 1

    return {"upserted": count}


@app.get("/api/canonical/genres")
async def list_genres(_=Depends(verify_key)):
    """Return the full controlled genre vocabulary (122 genres)."""
    return {"genres": CANONICAL_GENRES, "count": len(CANONICAL_GENRES)}


@app.get("/api/canonical/coverage")
async def genre_coverage(_=Depends(verify_key)):
    """Return every genre with its album count (0 for unstarted)."""
    rows = await fetch("""
        SELECT genre, COUNT(*) as albums
        FROM canonical_albums
        GROUP BY genre
    """)
    counts = {r["genre"]: r["albums"] for r in rows}
    coverage = [
        {"genre": g, "albums": counts.get(g, 0)}
        for g in CANONICAL_GENRES
    ]
    populated = sum(1 for c in coverage if c["albums"] > 0)
    return {
        "coverage": coverage,
        "populated": populated,
        "total_genres": len(CANONICAL_GENRES),
        "total_albums": sum(c["albums"] for c in coverage),
    }


@app.get("/api/canonical")
async def list_canonical(
    genre: Optional[str] = None,
    subgenre: Optional[str] = None,
    tier: Optional[str] = None,
    artist: Optional[str] = None,
    limit: int = Query(default=50, le=1000),
    offset: int = Query(default=0, ge=0),
    format: str = Query(default="compact", pattern="^(compact|json)$"),
    _=Depends(verify_key),
):
    clauses = []
    params = []

    if genre:
        params.append(genre)
        clauses.append(f"LOWER(genre) = LOWER(${len(params)})")
    if subgenre:
        params.append(subgenre)
        clauses.append(f"LOWER(subgenre) = LOWER(${len(params)})")
    if tier:
        params.append(tier)
        clauses.append(f"tier = ${len(params)}")
    if artist:
        params.append(f"%{artist}%")
        clauses.append(f"LOWER(artist) LIKE LOWER(${len(params)})")

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.extend([limit, offset])
    limit_idx = len(params) - 1
    offset_idx = len(params)

    rows = await fetch(f"""
        SELECT id, artist, album, year, genre, subgenre, tier, description, added_at
        FROM canonical_albums
        {where}
        ORDER BY genre, tier, year NULLS LAST
        LIMIT ${limit_idx} OFFSET ${offset_idx}
    """, *params)

    if format == "compact":
        return PlainTextResponse(compact_canonical(rows))
    return {"albums": rows, "count": len(rows), "filters": {"genre": genre, "subgenre": subgenre, "tier": tier, "artist": artist}}


@app.delete("/api/canonical/{album_id}")
async def delete_canonical(album_id: int, _=Depends(verify_key)):
    result = await execute("DELETE FROM canonical_albums WHERE id = $1", album_id)
    if result == "DELETE 0":
        raise HTTPException(404, "Album not found")
    return {"deleted": True, "id": album_id}


@app.delete("/api/canonical")
async def delete_canonical_by_match(
    artist: str = Query(...),
    album: str = Query(...),
    _=Depends(verify_key),
):
    result = await execute(
        "DELETE FROM canonical_albums WHERE LOWER(artist) = LOWER($1) AND LOWER(album) = LOWER($2)",
        artist, album,
    )
    if result == "DELETE 0":
        raise HTTPException(404, f"No canonical album found: {artist} - {album}")
    return {"deleted": True, "artist": artist, "album": album}


@app.post("/api/canonical/matches/refresh")
async def refresh_canonical_matches(_=Depends(verify_key)):
    """Batch-match canonical albums to listen events using normalized text.

    Matching strategy (applied in order, first match wins per event):
    1. Exact normalized: lowercase, strip articles/parens/punctuation
    2. Loose normalized: also strip common suffixes like (Deluxe), (Remaster)

    Populates canonical_listen_matches table for fast gap queries.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Ensure the normalize_album helper exists
        await conn.execute("""
            CREATE OR REPLACE FUNCTION normalize_album(name TEXT) RETURNS TEXT AS $$
                SELECT TRIM(REGEXP_REPLACE(
                    REGEXP_REPLACE(
                        LOWER(name),
                        '\\s*\\(([^)]*(?:deluxe|remaster|expanded|anniversary|bonus|special|edition|version|explicit|clean|mono|stereo|original)[^)]*)\\)',
                        '', 'gi'
                    ),
                    '[^a-z0-9\\s]', '', 'g'
                ))
            $$ LANGUAGE SQL IMMUTABLE
        """)

        # Truncate and repopulate
        await conn.execute("TRUNCATE canonical_listen_matches")

        # Pass 1: exact normalized match on both artist and album
        result1 = await conn.execute("""
            INSERT INTO canonical_listen_matches (canonical_id, event_id, match_method)
            SELECT DISTINCT ca.id, le.event_id, 'normalized'
            FROM canonical_albums ca
            JOIN listen_events le
                ON normalize_artist(le.raw_artist) = normalize_artist(ca.artist)
                AND normalize_album(le.raw_album) = normalize_album(ca.album)
            ON CONFLICT DO NOTHING
        """)
        pass1_count = int(result1.split()[-1]) if result1 else 0

        # Pass 2: loose match — strip ALL parentheticals from both sides
        # This catches "Zanaka (Deluxe)" matching "Zanaka",
        # or "OK Computer OKNOTOK 1997 2017" matching "OK Computer"
        await conn.execute("""
            CREATE OR REPLACE FUNCTION normalize_album_loose(name TEXT) RETURNS TEXT AS $$
                SELECT TRIM(REGEXP_REPLACE(
                    REGEXP_REPLACE(
                        LOWER(name),
                        '\\s*\\([^)]*\\)', '', 'g'
                    ),
                    '[^a-z0-9\\s]', '', 'g'
                ))
            $$ LANGUAGE SQL IMMUTABLE
        """)
        result2 = await conn.execute("""
            INSERT INTO canonical_listen_matches (canonical_id, event_id, match_method)
            SELECT DISTINCT ca.id, le.event_id, 'normalized_loose'
            FROM canonical_albums ca
            JOIN listen_events le
                ON normalize_artist(le.raw_artist) = normalize_artist(ca.artist)
                AND normalize_album_loose(le.raw_album) = normalize_album_loose(ca.album)
            WHERE NOT EXISTS (
                SELECT 1 FROM canonical_listen_matches clm
                WHERE clm.canonical_id = ca.id AND clm.event_id = le.event_id
            )
            ON CONFLICT DO NOTHING
        """)
        pass2_count = int(result2.split()[-1]) if result2 else 0

        # Get summary stats
        stats = await conn.fetchrow("""
            SELECT
                COUNT(DISTINCT canonical_id) AS matched_albums,
                COUNT(*) AS total_matches,
                COUNT(DISTINCT event_id) AS matched_events
            FROM canonical_listen_matches
        """)

        total_albums = await conn.fetchval("SELECT COUNT(*) FROM canonical_albums")

    return {
        "status": "ok",
        "pass1_normalized": pass1_count,
        "pass2_loose": pass2_count,
        "total_matches": stats["total_matches"],
        "matched_albums": stats["matched_albums"],
        "total_albums": total_albums,
        "unmatched_albums": total_albums - stats["matched_albums"],
        "matched_events": stats["matched_events"],
    }


@app.get("/api/canonical/gaps")
async def canonical_gaps(
    genre: Optional[str] = None,
    subgenre: Optional[str] = None,
    tier: Optional[str] = None,
    heard: Optional[str] = Query(default=None, pattern="^(true|false)$"),
    limit: int = Query(default=100, le=1000),
    offset: int = Query(default=0, ge=0),
    format: str = Query(default="compact", pattern="^(compact|json)$"),
    _=Depends(verify_key),
):
    clauses = []
    params = []

    if genre:
        params.append(genre)
        clauses.append(f"LOWER(ca.genre) = LOWER(${len(params)})")
    if subgenre:
        params.append(subgenre)
        clauses.append(f"LOWER(ca.subgenre) = LOWER(${len(params)})")
    if tier:
        params.append(tier)
        clauses.append(f"ca.tier = ${len(params)}")

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    # Build HAVING clause for heard filter
    having = ""
    if heard == "true":
        having = "HAVING COUNT(clm.event_id) > 0"
    elif heard == "false":
        having = "HAVING COUNT(clm.event_id) = 0"

    params.extend([limit, offset])
    limit_idx = len(params) - 1
    offset_idx = len(params)

    rows = await fetch_ro(f"""
        SELECT
            ca.id,
            ca.artist,
            ca.album,
            ca.year,
            ca.genre,
            ca.subgenre,
            ca.tier,
            ca.description,
            COUNT(clm.event_id)::int AS listen_count,
            COUNT(DISTINCT le.raw_title)::int AS unique_tracks,
            MIN(le.listened_at) AS first_listen,
            MAX(le.listened_at) AS last_listen
        FROM canonical_albums ca
        LEFT JOIN canonical_listen_matches clm ON clm.canonical_id = ca.id
        LEFT JOIN listen_events le ON le.event_id = clm.event_id
        {where}
        GROUP BY ca.id, ca.artist, ca.album, ca.year, ca.genre, ca.subgenre, ca.tier, ca.description
        {having}
        ORDER BY
            COUNT(clm.event_id) ASC,
            CASE ca.tier WHEN 'essential' THEN 1 WHEN 'important' THEN 2 WHEN 'deep' THEN 3 ELSE 4 END,
            ca.year NULLS LAST
        LIMIT ${limit_idx} OFFSET ${offset_idx}
    """, *params)

    results = []
    for r in rows:
        row_dict = dict(r)
        # Convert timestamps to ISO strings for JSON serialization
        if row_dict.get("first_listen"):
            row_dict["first_listen"] = row_dict["first_listen"].isoformat()
        else:
            row_dict["first_listen"] = None
        if row_dict.get("last_listen"):
            row_dict["last_listen"] = row_dict["last_listen"].isoformat()
        else:
            row_dict["last_listen"] = None
        results.append(row_dict)

    if format == "compact":
        return PlainTextResponse(compact_canonical_gaps(results))
    return {
        "albums": results,
        "count": len(results),
        "filters": {"genre": genre, "subgenre": subgenre, "tier": tier, "heard": heard},
    }


# ============================================================
# Admin: one-time migrations (auth required, write access)
# ============================================================

@app.post("/api/admin/migrate")
async def admin_migrate(body: dict, _=Depends(verify_key)):
    """Execute a DDL/admin SQL statement. For one-time migrations only."""
    sql = body.get("sql", "").strip()
    if not sql:
        raise HTTPException(status_code=400, detail="Missing 'sql' field")

    try:
        result = await execute(sql)
        return {"status": "ok", "result": str(result)}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
