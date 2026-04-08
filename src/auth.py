"""
API key authentication dependency.
"""

import os
from typing import Optional
from fastapi import Query, Header, HTTPException
from dotenv import load_dotenv

load_dotenv()

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
