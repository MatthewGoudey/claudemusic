"""
Compact response formatters for LLM-optimized output.

Each function takes the same data structure its endpoint returns
and produces a concise plain-text representation.
"""

from datetime import datetime, date, timezone, timedelta


def _day_abbr(d) -> str:
    """Get short day name from a date."""
    if isinstance(d, str):
        d = date.fromisoformat(d)
    if isinstance(d, datetime):
        d = d.date()
    return ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][d.weekday()]


def _fmt_date(d) -> str:
    """Format a date as 'Mon Apr 18'."""
    if d is None:
        return ""
    if isinstance(d, str):
        d = date.fromisoformat(d)
    if isinstance(d, datetime):
        d = d.date()
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return f"{_day_abbr(d)} {months[d.month - 1]} {d.day}"


def _fmt_ts(ts) -> str:
    """Format a timestamp as 'Apr 10 14:23'."""
    if ts is None:
        return ""
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return f"{months[ts.month - 1]} {ts.day:>2} {ts.hour:02d}:{ts.minute:02d}"


def compact_top_artists(rows: list[dict]) -> str:
    """'Artist (plays/Nt [Fs/Ps])' comma-separated."""
    parts = []
    for r in rows:
        name = r.get("raw_artist", "?")
        plays = r.get("listen_count", 0)
        tracks = r.get("unique_tracks", 0)
        full = r.get("full_sessions", 0)
        partial = r.get("partial_sessions", 0)
        sess = f" [{full}f/{partial}p]" if full or partial else ""
        parts.append(f"{name} ({plays}/{tracks}t{sess})")
    return ", ".join(parts)


def compact_top_tracks(rows: list[dict]) -> str:
    """'Artist - Title (plays)' comma-separated."""
    parts = []
    for r in rows:
        artist = r.get("raw_artist", "?")
        title = r.get("raw_title", "?")
        plays = r.get("listen_count", 0)
        parts.append(f"{artist} - {title} ({plays})")
    return ", ".join(parts)


def compact_top_albums(rows: list[dict]) -> str:
    """'Artist - Album (plays [Fs/Ps])' comma-separated."""
    parts = []
    for r in rows:
        artist = r.get("raw_artist", "?")
        album = r.get("raw_album", "?")
        plays = r.get("listen_count", 0)
        full = r.get("full_sessions", 0)
        partial = r.get("partial_sessions", 0)
        sess = f" [{full}f/{partial}p]" if full or partial else ""
        parts.append(f"{artist} - {album} ({plays}{sess})")
    return ", ".join(parts)


def compact_recent(rows: list[dict]) -> str:
    """'MMM DD HH:MM Artist - Track (Album)' one per line."""
    lines = []
    for r in rows:
        ts = _fmt_ts(r.get("listened_at"))
        artist = r.get("raw_artist", "?")
        title = r.get("raw_title", "?")
        album = r.get("raw_album", "")
        album_part = f" ({album})" if album else ""
        lines.append(f"{ts} {artist} - {title}{album_part}")
    return "\n".join(lines)


def compact_album_completion(rows: list[dict]) -> str:
    """'Artist - Album: heard/total tracks (pct%) [Fs/Ps]' one per line."""
    lines = []
    for r in rows:
        artist = r.get("raw_artist", "?")
        album = r.get("raw_album", "?")
        heard = r.get("tracks_heard", 0)
        total = r.get("total_tracks", 0)
        pct = round(r.get("completion", 0) * 100)
        full = r.get("full_sessions", 0)
        partial = r.get("partial_sessions", 0)
        sess = f" [{full}f/{partial}p]" if full or partial else ""
        lines.append(f"{artist} - {album}: {heard}/{total} tracks ({pct}%){sess}")
    return "\n".join(lines)


def compact_chicago_shows_match(matches: list[dict]) -> str:
    """'Day Date | Artist @ Venue | score: N | plays, tracks | [flags]' one per line."""
    lines = []
    for m in matches:
        show = m.get("show", {})
        stats = m.get("listening_stats", {})
        score = m.get("relevance_score", 0)

        show_date = show.get("show_date")
        day_date = _fmt_date(show_date) if show_date else "TBA"
        artist = show.get("artist_name", "?")
        venue = show.get("venue_name", "?")
        plays = stats.get("total_listens", 0)
        tracks = stats.get("unique_tracks", 0)

        flags = []
        venue_lower = (venue or "").lower()
        if "thalia" in venue_lower:
            flags.append("[THALIA HALL]")
        first_seen = show.get("first_seen")
        if first_seen:
            if isinstance(first_seen, str):
                first_seen = datetime.fromisoformat(first_seen)
            if isinstance(first_seen, datetime):
                now = datetime.now(timezone.utc)
                if (now - first_seen.replace(tzinfo=first_seen.tzinfo or timezone.utc)).days <= 7:
                    flags.append("[JUST ANNOUNCED]")
        presale_start = show.get("presale_start")
        if presale_start:
            if isinstance(presale_start, str):
                presale_start = datetime.fromisoformat(presale_start)
            if isinstance(presale_start, datetime):
                now = datetime.now(timezone.utc)
                ps = presale_start.replace(tzinfo=presale_start.tzinfo or timezone.utc)
                if ps > now and (ps - now).days <= 14:
                    flags.append("[PRESALE SOON]")

        flag_str = " " + " ".join(flags) if flags else ""
        lines.append(f"{day_date} | {artist} @ {venue} | score: {score} | {plays} plays, {tracks} tracks{flag_str}")
    return "\n".join(lines)


def compact_discover(data: dict) -> str:
    """'Artist (sim: score, plays) [genres] — show info' one per line."""
    lines = []
    for a in data.get("similar_artists", []):
        name = a.get("name", "?")
        sim = round(a.get("similarity_score", 0), 2)
        plays = a.get("listen_count", 0)
        tags = a.get("tags", [])[:3]
        tag_str = f" [{', '.join(tags)}]" if tags else ""

        shows = a.get("upcoming_shows", [])
        if shows:
            s = shows[0]
            show_date = s.get("date", "")
            venue = s.get("venue_name", "?")
            d = _fmt_date(show_date) if show_date else "TBA"
            show_str = f"show: {d} @ {venue}"
        else:
            show_str = "no upcoming shows"

        lines.append(f"{name} (sim: {sim}, {plays} plays){tag_str} — {show_str}")
    return "\n".join(lines)


def compact_chicago_shows(shows: list[dict]) -> str:
    """'Day Date | Artist @ Venue | time | price | [flags]' one per line."""
    lines = []
    for show in shows:
        show_date = show.get("show_date")
        day_date = _fmt_date(show_date) if show_date else "TBA"
        artist = show.get("artist_name", "?")
        venue = show.get("venue_name", "?")
        time_val = show.get("show_time")
        time_str = str(time_val)[:5] if time_val else ""
        price = show.get("ticket_price", "")
        price_str = f" | {price}" if price else ""
        time_part = f" | {time_str}" if time_str else ""
        lines.append(f"{day_date} | {artist} @ {venue}{time_part}{price_str}")
    return "\n".join(lines)


def compact_canonical(rows: list[dict]) -> str:
    """'Artist - Album (year) [genre/subgenre] tier — description' one per line."""
    lines = []
    for r in rows:
        artist = r.get("artist", "?")
        album = r.get("album", "?")
        year = r.get("year")
        year_str = f" ({year})" if year else ""
        genre = r.get("genre", "")
        subgenre = r.get("subgenre")
        genre_str = f"{genre}/{subgenre}" if subgenre else genre
        tier = r.get("tier", "")
        desc = r.get("description", "")
        desc_str = f" — {desc}" if desc else ""
        lines.append(f"{artist} - {album}{year_str} [{genre_str}] {tier}{desc_str}")
    return "\n".join(lines)


def compact_canonical_gaps(rows: list[dict]) -> str:
    """Split canonical albums into UNHEARD/HEARD sections with listen stats."""
    unheard = []
    heard = []
    for r in rows:
        artist = r.get("artist", "?")
        album = r.get("album", "?")
        year = r.get("year")
        year_str = f" ({year})" if year else ""
        genre = r.get("genre", "")
        subgenre = r.get("subgenre")
        genre_str = f"{genre}/{subgenre}" if subgenre else genre
        tier = r.get("tier", "")
        desc = r.get("description", "")
        desc_str = f" — {desc}" if desc else ""
        listen_count = r.get("listen_count", 0)

        if listen_count == 0:
            unheard.append(f"  {artist} - {album}{year_str} [{genre_str}] {tier}{desc_str}")
        else:
            unique_tracks = r.get("unique_tracks", 0)
            last_listen = r.get("last_listen")
            last_str = _fmt_date(last_listen) if last_listen else ""
            full = r.get("full_sessions", 0)
            partial = r.get("partial_sessions", 0)
            stats = f"{listen_count} plays, {unique_tracks} tracks"
            if full or partial:
                stats += f", {full}f/{partial}p sessions"
            if last_str:
                stats += f", last heard {last_str}"
            heard.append(f"  {artist} - {album}{year_str} [{genre_str}] {tier} — {stats}")

    sections = []
    if unheard:
        sections.append("UNHEARD:\n" + "\n".join(unheard))
    if heard:
        sections.append("HEARD:\n" + "\n".join(heard))
    return "\n\n".join(sections)


def _source_tags(sources: list[dict]) -> str:
    """Format source list as compact tags: [RS#1, 1001#42, AOTY]."""
    tags = []
    abbrev = {"rolling_stone_500": "RS", "1001_albums": "1001", "aoty": "AOTY", "canonical": "Canon", "manual": "Manual"}
    for s in sources:
        name = abbrev.get(s.get("source", ""), s.get("source", "?"))
        rank = s.get("rank")
        tags.append(f"{name}#{rank}" if rank else name)
    return "[" + ", ".join(tags) + "]" if tags else ""


def compact_checklist(rows: list[dict]) -> str:
    """'Artist - Album (year) [RS#1, 1001#42] — 47 plays, 3f/1p' one per line."""
    lines = []
    for r in rows:
        artist = r.get("artist", "?")
        album = r.get("album", "?")
        year = r.get("year")
        year_str = f" ({year})" if year else ""
        src = _source_tags(r.get("sources", []))
        listen_count = r.get("listen_count", 0)
        full = r.get("full_sessions", 0)
        partial = r.get("partial_sessions", 0)
        if listen_count > 0:
            sess = f", {full}f/{partial}p" if full or partial else ""
            stats = f" — {listen_count} plays{sess}"
        else:
            stats = " — unheard"
        lines.append(f"{artist} - {album}{year_str} {src}{stats}")
    return "\n".join(lines)


def compact_checklist_gaps(rows: list[dict]) -> str:
    """Unheard checklist albums with source tags."""
    lines = []
    for r in rows:
        artist = r.get("artist", "?")
        album = r.get("album", "?")
        year = r.get("year")
        year_str = f" ({year})" if year else ""
        src = _source_tags(r.get("sources", []))
        lines.append(f"{artist} - {album}{year_str} {src}")
    return "\n".join(lines)
