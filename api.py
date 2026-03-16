"""
Music Pipeline API — read-only endpoints for Claude to query via web_fetch.

Deploy to Render/Railway/Vercel free tier. Claude can then call these
endpoints from any conversation to see your listening data.

Usage:
    # Local dev
    uvicorn src.api:app --reload --port 8000

    # Then Claude can call:
    # https://your-app.onrender.com/api/top-artists?days=30&limit=20
"""

import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Query, Header
from fastapi.responses import JSONResponse
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Music Listening API", version="0.1.0")

# Simple API key auth — keeps your data private.
# Set API_SECRET in your .env and pass it as ?key= or Authorization header.
API_SECRET = os.environ.get("API_SECRET", "changeme")


def verify_key(
    key: Optional[str] = Query(None),
    authorization: Optional[str] = Header(None),
):
    """Check API key from query param or Authorization header."""
    token = key or (authorization.replace("Bearer ", "") if authorization else None)
    if token != API_SECRET:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return token


def get_conn():
    """Get a database connection. Caller must close it."""
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise HTTPException(status_code=500, detail="DATABASE_URL not configured")
    return psycopg2.connect(db_url)


def run_query(sql: str, params: tuple = ()) -> list[dict]:
    """Execute a read-only query and return results as list of dicts."""
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


# ============================================================
# Health check
# ============================================================

@app.get("/api/health")
def health():
    """No auth required — just confirms the API is running."""
    return {"status": "ok"}


# ============================================================
# Summary / overview
# ============================================================

@app.get("/api/summary")
def summary(_=Depends(verify_key)):
    """High-level stats about the entire dataset."""
    rows = run_query("""
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
    platform_breakdown = run_query("""
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
def top_artists(
    days: Optional[int] = None,
    year: Optional[int] = None,
    limit: int = Query(default=25, le=200),
    _=Depends(verify_key),
):
    """
    Top artists by listen count.
    Filter by days (last N days) or year (specific year).
    """
    where = []
    params = []

    if days:
        where.append("listened_at > now() - interval '%s days'")
        params.append(days)
    elif year:
        where.append("EXTRACT(YEAR FROM listened_at) = %s")
        params.append(year)

    where_clause = ("WHERE " + " AND ".join(where)) if where else ""

    rows = run_query(f"""
        SELECT
            raw_artist,
            COUNT(*) AS listen_count,
            COUNT(DISTINCT raw_title) AS unique_tracks,
            COUNT(DISTINCT raw_album) FILTER (WHERE raw_album IS NOT NULL) AS unique_albums,
            MIN(listened_at) AS first_listen,
            MAX(listened_at) AS last_listen
        FROM listen_events
        {where_clause}
        GROUP BY raw_artist
        ORDER BY listen_count DESC
        LIMIT %s
    """, tuple(params) + (limit,))
    return {"artists": rows, "filters": {"days": days, "year": year}}


# ============================================================
# Top tracks
# ============================================================

@app.get("/api/top-tracks")
def top_tracks(
    days: Optional[int] = None,
    year: Optional[int] = None,
    artist: Optional[str] = None,
    limit: int = Query(default=25, le=200),
    _=Depends(verify_key),
):
    """Top tracks by listen count."""
    where = []
    params = []

    if days:
        where.append("listened_at > now() - interval '%s days'")
        params.append(days)
    elif year:
        where.append("EXTRACT(YEAR FROM listened_at) = %s")
        params.append(year)
    if artist:
        where.append("LOWER(raw_artist) = LOWER(%s)")
        params.append(artist)

    where_clause = ("WHERE " + " AND ".join(where)) if where else ""

    rows = run_query(f"""
        SELECT
            raw_title,
            raw_artist,
            raw_album,
            COUNT(*) AS listen_count,
            MIN(listened_at) AS first_listen,
            MAX(listened_at) AS last_listen
        FROM listen_events
        {where_clause}
        GROUP BY raw_title, raw_artist, raw_album
        ORDER BY listen_count DESC
        LIMIT %s
    """, tuple(params) + (limit,))
    return {"tracks": rows, "filters": {"days": days, "year": year, "artist": artist}}


# ============================================================
# Top albums
# ============================================================

@app.get("/api/top-albums")
def top_albums(
    days: Optional[int] = None,
    year: Optional[int] = None,
    artist: Optional[str] = None,
    limit: int = Query(default=25, le=200),
    _=Depends(verify_key),
):
    """Top albums by listen count."""
    where = ["raw_album IS NOT NULL"]
    params = []

    if days:
        where.append("listened_at > now() - interval '%s days'")
        params.append(days)
    elif year:
        where.append("EXTRACT(YEAR FROM listened_at) = %s")
        params.append(year)
    if artist:
        where.append("LOWER(raw_artist) = LOWER(%s)")
        params.append(artist)

    where_clause = "WHERE " + " AND ".join(where)

    rows = run_query(f"""
        SELECT
            raw_album,
            raw_artist,
            COUNT(*) AS listen_count,
            COUNT(DISTINCT raw_title) AS unique_tracks,
            MIN(listened_at) AS first_listen,
            MAX(listened_at) AS last_listen
        FROM listen_events
        {where_clause}
        GROUP BY raw_album, raw_artist
        ORDER BY listen_count DESC
        LIMIT %s
    """, tuple(params) + (limit,))
    return {"albums": rows, "filters": {"days": days, "year": year, "artist": artist}}


# ============================================================
# Listening timeline
# ============================================================

@app.get("/api/timeline")
def timeline(
    granularity: str = Query(default="month", regex="^(day|week|month|year)$"),
    days: Optional[int] = None,
    year: Optional[int] = None,
    _=Depends(verify_key),
):
    """Listen counts over time at day/week/month/year granularity."""
    trunc_map = {
        "day": "day",
        "week": "week",
        "month": "month",
        "year": "year",
    }
    trunc = trunc_map[granularity]

    where = []
    params = []

    if days:
        where.append("listened_at > now() - interval '%s days'")
        params.append(days)
    elif year:
        where.append("EXTRACT(YEAR FROM listened_at) = %s")
        params.append(year)

    where_clause = ("WHERE " + " AND ".join(where)) if where else ""

    rows = run_query(f"""
        SELECT
            DATE_TRUNC('{trunc}', listened_at) AS period,
            COUNT(*) AS listen_count,
            COUNT(DISTINCT raw_artist) AS unique_artists,
            COUNT(DISTINCT raw_title || '|||' || raw_artist) AS unique_tracks
        FROM listen_events
        {where_clause}
        GROUP BY period
        ORDER BY period
    """, tuple(params))
    return {"timeline": rows, "granularity": granularity}


# ============================================================
# Recent listens
# ============================================================

@app.get("/api/recent")
def recent(
    limit: int = Query(default=50, le=500),
    _=Depends(verify_key),
):
    """Most recent listens."""
    rows = run_query("""
        SELECT raw_title, raw_artist, raw_album, platform, source_app,
               listened_at, content_type, confidence
        FROM listen_events
        ORDER BY listened_at DESC
        LIMIT %s
    """, (limit,))
    return {"listens": rows}


# ============================================================
# Full album listens
# ============================================================

@app.get("/api/full-album-listens")
def full_album_listens(
    days: Optional[int] = None,
    year: Optional[int] = None,
    threshold: float = Query(default=0.8, ge=0.5, le=1.0),
    max_gap_minutes: int = Query(default=120),
    limit: int = Query(default=50, le=200),
    _=Depends(verify_key),
):
    """
    Detect full album listens — sessions where you played >= threshold
    of an album's tracks within a time window.

    This is a heuristic: groups listens by (raw_artist, raw_album),
    finds clusters where consecutive tracks are within max_gap_minutes
    of each other, and checks if the cluster covers >= threshold of
    the album's known tracks.

    Note: accuracy improves as track resolution progresses, since
    album_tracks gives the true track count. For now, we estimate
    album size from the max distinct tracks we've ever seen for that album.
    """
    where = ["raw_album IS NOT NULL"]
    params = []

    if days:
        where.append("listened_at > now() - interval '%s days'")
        params.append(days)
    elif year:
        where.append("EXTRACT(YEAR FROM listened_at) = %s")
        params.append(year)

    where_clause = "WHERE " + " AND ".join(where)

    # This query finds albums where we played a high proportion of tracks
    # in a single session (approximated by same day).
    rows = run_query(f"""
        WITH album_sizes AS (
            SELECT raw_artist, raw_album,
                   COUNT(DISTINCT raw_title) AS total_tracks
            FROM listen_events
            WHERE raw_album IS NOT NULL
            GROUP BY raw_artist, raw_album
        ),
        daily_album_plays AS (
            SELECT
                e.raw_artist,
                e.raw_album,
                DATE(e.listened_at) AS listen_date,
                COUNT(DISTINCT e.raw_title) AS tracks_played,
                MIN(e.listened_at) AS session_start,
                MAX(e.listened_at) AS session_end,
                ARRAY_AGG(DISTINCT e.raw_title ORDER BY e.raw_title) AS tracks
            FROM listen_events e
            {where_clause}
            GROUP BY e.raw_artist, e.raw_album, DATE(e.listened_at)
        )
        SELECT
            d.raw_artist,
            d.raw_album,
            d.listen_date,
            d.tracks_played,
            a.total_tracks AS album_total_tracks,
            ROUND(d.tracks_played::numeric / NULLIF(a.total_tracks, 0), 2) AS completion,
            d.session_start,
            d.session_end
        FROM daily_album_plays d
        JOIN album_sizes a ON a.raw_artist = d.raw_artist AND a.raw_album = d.raw_album
        WHERE a.total_tracks >= 3
          AND d.tracks_played::numeric / NULLIF(a.total_tracks, 0) >= %s
        ORDER BY d.listen_date DESC
        LIMIT %s
    """, tuple(params) + (threshold, limit))
    return {
        "full_album_listens": rows,
        "filters": {"days": days, "year": year, "threshold": threshold},
    }


# ============================================================
# Artist deep dive
# ============================================================

@app.get("/api/artist/{artist_name}")
def artist_detail(
    artist_name: str,
    _=Depends(verify_key),
):
    """Detailed stats for a specific artist."""
    listens = run_query("""
        SELECT COUNT(*) AS total_listens,
               COUNT(DISTINCT raw_title) AS unique_tracks,
               COUNT(DISTINCT raw_album) FILTER (WHERE raw_album IS NOT NULL) AS unique_albums,
               MIN(listened_at) AS first_listen,
               MAX(listened_at) AS last_listen
        FROM listen_events
        WHERE LOWER(raw_artist) = LOWER(%s)
    """, (artist_name,))

    top_tracks = run_query("""
        SELECT raw_title, raw_album, COUNT(*) AS listen_count
        FROM listen_events
        WHERE LOWER(raw_artist) = LOWER(%s)
        GROUP BY raw_title, raw_album
        ORDER BY listen_count DESC
        LIMIT 20
    """, (artist_name,))

    by_year = run_query("""
        SELECT EXTRACT(YEAR FROM listened_at)::int AS year,
               COUNT(*) AS listen_count
        FROM listen_events
        WHERE LOWER(raw_artist) = LOWER(%s)
        GROUP BY year
        ORDER BY year
    """, (artist_name,))

    return {
        "artist": artist_name,
        "stats": listens[0] if listens else {},
        "top_tracks": top_tracks,
        "by_year": by_year,
    }


# ============================================================
# Ad-hoc query (power user — run arbitrary read-only SQL)
# ============================================================

@app.post("/api/query")
def adhoc_query(
    body: dict,
    _=Depends(verify_key),
):
    """
    Run an arbitrary read-only SQL query.

    Body: {"sql": "SELECT ...", "limit": 100}

    Safety: only SELECT statements allowed.
    """
    sql = body.get("sql", "").strip()
    limit = body.get("limit", 100)

    if not sql:
        raise HTTPException(status_code=400, detail="Missing 'sql' field")

    # Basic safety — only allow SELECT
    first_word = sql.split()[0].upper() if sql.split() else ""
    if first_word not in ("SELECT", "WITH", "EXPLAIN"):
        raise HTTPException(status_code=400, detail="Only SELECT queries allowed")

    # Prevent destructive keywords anywhere in the query
    dangerous = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "CREATE", "GRANT", "REVOKE"]
    sql_upper = sql.upper()
    for word in dangerous:
        if word in sql_upper:
            raise HTTPException(status_code=400, detail=f"Query contains forbidden keyword: {word}")

    # Add limit if not present
    if "LIMIT" not in sql_upper:
        sql = sql.rstrip(";") + f" LIMIT {limit}"

    rows = run_query(sql)
    return {"results": rows, "count": len(rows)}
