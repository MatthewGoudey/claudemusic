"""
Controlled genre vocabulary for canonical_albums.
122 genres organized by family. Family is for human readability only — not stored in DB.
Only the genre string goes in the `genre` column. Subgenre is free-text.
"""

CANONICAL_GENRES: list[str] = [
    # ── Hip-Hop ──
    "East Coast Hip-Hop",
    "West Coast Hip-Hop",
    "Southern Hip-Hop",
    "Underground Hip-Hop",
    "Trap",
    "SoundCloud Rap",
    "French Hip-Hop",
    "Latin Hip-Hop",
    "Japanese Hip-Hop",
    "Korean Hip-Hop",

    # ── Rock — Classic Era ──
    "Classic Rock",
    "Psychedelic Rock",
    "Progressive Rock",
    "Blues",
    "Garage Rock",

    # ── Rock — Punk Lineage ──
    "Punk",
    "Post-Punk",
    "Goth",
    "Emo",
    "Screamo",
    "Post-Hardcore",
    "Grunge",
    "Pop Punk",

    # ── Rock — Alternative & Indie ──
    "Indie Rock",
    "Britpop",
    "Art Rock",
    "Art Pop",
    "Lo-Fi Rock",
    "Bedroom Pop",
    "Post-Rock",
    "Shoegaze",
    "Math Rock",
    "Power Pop",
    "Noise Rock",

    # ── Rock — Heavy ──
    "Classic Metal",
    "NWOBHM",
    "Thrash Metal",
    "Death Metal",
    "Black Metal",
    "Doom Metal",
    "Progressive Metal",
    "Post-Metal",
    "Metalcore",

    # ── Industrial ──
    "Industrial",

    # ── Electronic ──
    "House",
    "Techno",
    "Drum And Bass",
    "UK Bass",
    "Ambient",
    "IDM",
    "Krautrock",
    "Disco",
    "Footwork",
    "Trance",
    "Trip-Hop",
    "Japanese Electronic",

    # ── Electronic — Pop-Adjacent ──
    "New Wave",
    "Vaporwave",
    "Hyperpop",
    "Phonk",

    # ── Soul, R&B, Funk ──
    "Classic Soul",
    "Contemporary R&B",
    "Funk",
    "Quiet Storm",

    # ── Country & Folk ──
    "Traditional Country",
    "Outlaw Country",
    "Country Rock",
    "Americana",
    "Bluegrass",
    "Traditional Folk",
    "Singer-Songwriter",

    # ── Jazz ──
    "Early Jazz",
    "Bebop",
    "Modal Jazz",
    "Free Jazz",
    "Jazz Fusion",
    "Modern Jazz",
    "Japanese Jazz",
    "Latin Jazz",

    # ── Latin ──
    "Regional Mexican",
    "Corridos Tumbados",
    "Cumbia",
    "Salsa",
    "Latin Pop",
    "Reggaeton",
    "Rock En Espanol",
    "Nueva Cancion",
    "Bolero",

    # ── Brazilian ──
    "Bossa Nova",
    "Samba",
    "Tropicalia",
    "MPB",
    "Sertanejo",
    "Baile Funk",

    # ── African ──
    "Afrobeat",
    "Afrobeats",
    "Ethiopian Jazz",

    # ── Caribbean ──
    "Reggae",
    "Dancehall",

    # ── Japanese ──
    "City Pop",
    "J-Rock",
    "Shibuya-Kei",
    "Visual Kei",
    "Enka",
    "Japanese Noise",
    "Anison",

    # ── Korean ──
    "K-Pop",
    "K-Indie",
    "Trot",

    # ── French ──
    "Chanson",
    "French Touch",
    "French Pop",

    # ── Pop ──
    "Classic Pop",
    "Modern Pop",

    # ── Classical & Composition ──
    "Orchestral",
    "Chamber Music",
    "Modern Classical",
    "Film Score",
    "Video Game OST",

    # ── Other ──
    "Ska",
    "Gospel",
    "Musical Theater",
]

CANONICAL_GENRE_SET: set[str] = set(CANONICAL_GENRES)
CANONICAL_GENRE_LOWER: dict[str, str] = {g.lower(): g for g in CANONICAL_GENRES}
