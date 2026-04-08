"""
Album session detection engine.

Uses gap-based clustering: consecutive listens from the same artist+album
where no gap between adjacent tracks exceeds a threshold (default 30 minutes).
"""

from datetime import datetime


def detect_sessions(
    listens: list[dict],
    total_tracks: int,
    gap_minutes: int = 30,
) -> list[dict]:
    """Detect album listening sessions from a chronologically ordered list of listens.

    Args:
        listens: List of dicts with at least 'listened_at' (datetime) and 'raw_title' keys,
                 ordered by listened_at ASC.
        total_tracks: Total tracks on the album (from album_tracklist or heuristic).
        gap_minutes: Max gap between adjacent listens before starting a new session.

    Returns:
        List of session dicts with session_start, session_end, tracks_played,
        total_tracks, completion, session_type.
    """
    if not listens:
        return []

    gap_seconds = gap_minutes * 60
    sessions = []
    current_session_listens = [listens[0]]

    for i in range(1, len(listens)):
        prev_time = listens[i - 1]["listened_at"]
        curr_time = listens[i]["listened_at"]

        if isinstance(prev_time, str):
            prev_time = datetime.fromisoformat(prev_time)
        if isinstance(curr_time, str):
            curr_time = datetime.fromisoformat(curr_time)

        gap = (curr_time - prev_time).total_seconds()

        if gap > gap_seconds:
            session = _build_session(current_session_listens, total_tracks)
            if session is not None:
                sessions.append(session)
            current_session_listens = [listens[i]]
        else:
            current_session_listens.append(listens[i])

    if current_session_listens:
        session = _build_session(current_session_listens, total_tracks)
        if session is not None:
            sessions.append(session)

    return sessions


def _build_session(listens: list[dict], total_tracks: int) -> dict:
    """Build a session dict from a cluster of listens."""
    distinct_tracks = len({l["raw_title"] for l in listens})
    completion = round(distinct_tracks / total_tracks, 2) if total_tracks > 0 else 0.0

    start = listens[0]["listened_at"]
    end = listens[-1]["listened_at"]
    if isinstance(start, str):
        start = datetime.fromisoformat(start)
    if isinstance(end, str):
        end = datetime.fromisoformat(end)

    # Classification:
    #   full:    completion >= 0.8
    #   partial: completion >= 0.25 AND tracks >= 3
    #   discard: below threshold (not a real session)
    if completion >= 0.8:
        session_type = "full"
    elif completion >= 0.25 and distinct_tracks >= 3:
        session_type = "partial"
    else:
        return None  # Below threshold — not a session

    return {
        "session_start": start.isoformat(),
        "session_end": end.isoformat(),
        "tracks_played": distinct_tracks,
        "total_tracks": total_tracks,
        "completion": completion,
        "session_type": session_type,
    }
