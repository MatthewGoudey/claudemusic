"""
Tests for the album tracklist resolver — name cleaning and ground truth validation.

Unit tests can run without network access.
Integration tests (marked with @pytest.mark.integration) hit the live MusicBrainz API.

Usage:
    pytest tests/test_resolver.py -v                    # unit tests only
    pytest tests/test_resolver.py -v -m integration     # integration tests only
    pytest tests/test_resolver.py -v --run-integration  # all tests
"""

import sys
import os
import pytest

# Add the repo root to sys.path so we can import src.*
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.musicbrainz import _clean_album_name, _clean_artist_name, _split_artists


# ============================================================
# Unit tests: album name cleaning
# ============================================================

class TestCleanAlbumName:
    def test_bracket_suffix(self):
        assert _clean_album_name("Album [Deluxe Edition]") == "Album"

    def test_parenthetical_edition(self):
        assert _clean_album_name("Album (Remastered Edition)") == "Album"
        assert _clean_album_name("Album (Deluxe)") == "Album"
        assert _clean_album_name("Album (Expanded Edition)") == "Album"
        assert _clean_album_name("Album (Anniversary Edition)") == "Album"

    def test_dash_edition(self):
        assert _clean_album_name("Album - Deluxe") == "Album"
        assert _clean_album_name("Album - Remastered") == "Album"
        assert _clean_album_name("Dreamland - Real Life Edition") == "Dreamland"

    def test_ep_suffix(self):
        assert _clean_album_name("Hippie Castle EP") == "Hippie Castle"
        assert _clean_album_name("Lately EP") == "Lately"

    def test_feat_suffix(self):
        assert _clean_album_name("Song (feat. Artist)") == "Song"
        assert _clean_album_name("Song (feat Artist)") == "Song"

    def test_complete_suffix(self):
        assert _clean_album_name("Operation: Doomsday (Complete)") == "Operation: Doomsday"

    def test_bw_suffix(self):
        assert _clean_album_name("Beautiful Strangers b/w No Place to Fall") == "Beautiful Strangers"

    def test_wrapping_quotes(self):
        assert _clean_album_name('"Awaken, My Love!"') == "Awaken, My Love!"
        assert _clean_album_name("'Album Name'") == "Album Name"

    def test_soundtrack_brackets(self):
        assert _clean_album_name("Game [Original Game Soundtrack]") == "Game"

    def test_no_change_needed(self):
        assert _clean_album_name("OK Computer") == "OK Computer"
        assert _clean_album_name("Nevermind") == "Nevermind"

    def test_preserves_internal_parens(self):
        # Should NOT strip parenthetical that isn't an edition
        assert _clean_album_name("Three Ringz (Thr33 Ringz)") == "Three Ringz (Thr33 Ringz)"

    def test_multiple_patterns(self):
        assert _clean_album_name('"Album Name" [Deluxe Edition]') == "Album Name"


# ============================================================
# Unit tests: artist name cleaning
# ============================================================

class TestCleanArtistName:
    def test_ms_prefix(self):
        assert _clean_artist_name("Ms. Lauryn Hill") == "Lauryn Hill"
        assert _clean_artist_name("Ms Lauryn Hill") == "Lauryn Hill"

    def test_mr_prefix(self):
        assert _clean_artist_name("Mr. Mister") == "Mister"

    def test_no_prefix(self):
        assert _clean_artist_name("Radiohead") == "Radiohead"
        assert _clean_artist_name("DJ Shadow") == "DJ Shadow"  # DJ is NOT stripped


# ============================================================
# Unit tests: artist splitting
# ============================================================

class TestSplitArtists:
    def test_simple_comma(self):
        result = _split_artists("Artist1, Artist2")
        assert result == ["Artist1, Artist2", "Artist1"]

    def test_tyler_the_creator_guard(self):
        # Should NOT split — "The Creator" starts with "The"
        result = _split_artists("Tyler, The Creator")
        assert result == ["Tyler, The Creator"]

    def test_feat_split(self):
        result = _split_artists("Main Artist feat. Other Artist")
        assert "Main Artist" in result
        assert result[0] == "Main Artist feat. Other Artist"

    def test_ft_split(self):
        result = _split_artists("Main Artist ft. Other")
        assert "Main Artist" in result

    def test_ampersand_split(self):
        result = _split_artists("Artist1 & Artist2")
        assert "Artist1" in result

    def test_no_split_needed(self):
        result = _split_artists("Radiohead")
        assert result == ["Radiohead"]

    def test_with_split(self):
        result = _split_artists("Artist1 with Artist2")
        assert "Artist1" in result


# ============================================================
# Ground truth: expected resolutions
# ============================================================

GROUND_TRUTH = [
    # (artist, album, expected_track_count, tolerance, expected_release_type)
    ("Radiohead", "OK Computer", 12, 1, "Album"),
    ("Pink Floyd", "Wish You Were Here", 5, 0, "Album"),
    ("Fleetwood Mac", "Rumours", 11, 1, "Album"),
    ("Kendrick Lamar", "GNX", 12, 1, "Album"),
    ("Taylor Swift", "Midnights", 13, 1, "Album"),
    ("The Beatles", "Abbey Road", 17, 1, "Album"),
    ("Kanye West", "My Beautiful Dark Twisted Fantasy", 13, 1, "Album"),
    ("Nirvana", "Nevermind", 12, 2, "Album"),
    # These should resolve after normalization improvements
    ("Childish Gambino", '"Awaken, My Love!"', 11, 1, "Album"),
    ("Ms. Lauryn Hill", "The Miseducation of Lauryn Hill", 16, 1, "Album"),
    ("Glass Animals", "Dreamland - Real Life Edition", 12, 4, "Album"),  # standard or deluxe
    ("MF DOOM", "Operation: Doomsday (Complete)", 19, 5, "Album"),
    ("Magic City Hippies", "Hippie Castle EP", 6, 1, "EP"),
]


@pytest.mark.integration
class TestGroundTruthResolution:
    """Integration tests that hit the live MusicBrainz API.

    Run with: pytest tests/test_resolver.py -v -m integration
    """

    @pytest.fixture
    def client(self):
        import httpx
        return httpx.AsyncClient(
            headers={
                "User-Agent": "MusicListeningPipeline/0.2 (personal project; https://github.com)",
                "Accept": "application/json",
            },
            timeout=30,
        )

    @pytest.mark.parametrize(
        "artist,album,expected_count,tolerance,expected_type",
        GROUND_TRUTH,
        ids=[f"{a}-{b}" for a, b, *_ in GROUND_TRUTH],
    )
    @pytest.mark.asyncio
    async def test_resolution(self, client, artist, album, expected_count, tolerance, expected_type):
        import asyncio
        from src.musicbrainz import resolve_album_tracklist

        await asyncio.sleep(1.2)  # Rate limit

        async with client:
            result = await resolve_album_tracklist(client, artist, album)

        assert result is not None, f"Failed to resolve: {artist} - {album}"
        count, rtype = result
        assert abs(count - expected_count) <= tolerance, (
            f"{artist} - {album}: got {count} tracks, expected {expected_count} +/- {tolerance}"
        )
        assert rtype == expected_type, (
            f"{artist} - {album}: got type '{rtype}', expected '{expected_type}'"
        )
