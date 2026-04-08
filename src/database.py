"""
Database connection pool using asyncpg.

Replaces the per-request psycopg2.connect() pattern with a shared pool.
"""

import os
import asyncpg
from dotenv import load_dotenv

load_dotenv()

_pool: asyncpg.Pool | None = None
_ro_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    """Get or create the main connection pool."""
    global _pool
    if _pool is None:
        db_url = os.environ.get("DATABASE_URL")
        if not db_url:
            raise RuntimeError("DATABASE_URL not configured")
        _pool = await asyncpg.create_pool(db_url, min_size=2, max_size=10)
    return _pool


async def get_ro_pool() -> asyncpg.Pool:
    """Get or create the read-only connection pool for /api/query.

    Uses DATABASE_URL_READONLY if set, otherwise falls back to main pool.
    The read-only Postgres role should only have SELECT permissions.
    """
    global _ro_pool
    if _ro_pool is None:
        ro_url = os.environ.get("DATABASE_URL_READONLY")
        if ro_url:
            _ro_pool = await asyncpg.create_pool(ro_url, min_size=1, max_size=5)
        else:
            _ro_pool = await get_pool()
    return _ro_pool


async def close_pools():
    """Close all connection pools. Called on app shutdown."""
    global _pool, _ro_pool
    if _ro_pool is not None and _ro_pool is not _pool:
        await _ro_pool.close()
        _ro_pool = None
    if _pool is not None:
        await _pool.close()
        _pool = None


async def fetch(sql: str, *args) -> list[dict]:
    """Execute a query and return results as list of dicts."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
        return [dict(r) for r in rows]


async def fetchrow(sql: str, *args) -> dict | None:
    """Execute a query and return a single row as dict."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(sql, *args)
        return dict(row) if row else None


async def fetchval(sql: str, *args):
    """Execute a query and return a single value."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(sql, *args)


async def execute(sql: str, *args):
    """Execute a statement (INSERT, UPDATE, etc.)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await conn.execute(sql, *args)


async def fetch_ro(sql: str, *args) -> list[dict]:
    """Execute a read-only query using the RO pool."""
    pool = await get_ro_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
        return [dict(r) for r in rows]
