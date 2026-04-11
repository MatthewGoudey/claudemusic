"""
Exploration mode specifications served as plain text by /api/modes/{mode_id}.

Each mode is a procedural instruction set consumed by Claude at runtime.
"""

MODES = {
    "finish": {
        "name": "Finish What You Started",
        "description": "Albums with 3+ tracks heard, under 80% completion, never fully completed.",
        "spec": """\
Mode: finish_what_you_started

Albums with 3+ tracks heard, under 80% completion, never fully completed.
Prioritize albums where the user had identifiable partial sessions (actively sat down and stopped).

API Action:
/api/album-completion min_completion=0.25 max_completion=0.79, sort by completion desc.
Cross-reference /api/album-sessions?session_type=partial for abandoned mid-listen sessions — these are higher priority.

Source Action:
Search AOTY and RYM for the album's rating. Prioritize the most critically acclaimed unfinished albums.
If poorly rated everywhere, note that — sometimes dropping it was the right call.

Output:
1 release with: completion stats, why it's worth finishing, the first unheard track to restart from, source ratings.""",
    },
    "disco": {
        "name": "Deep Discography",
        "description": "Pick a high-play artist, find their barely-touched or skipped albums.",
        "spec": """\
Mode: deep_discography

Pick a high-play artist (500+ plays) and find their albums the user has barely touched or skipped entirely.

API Action:
/api/top-artists?limit=20 to identify high-play artists.
/api/album-completion?artist=[name] for per-album coverage.
/api/top-tracks?artist=[name] to understand if user cherry-picked singles or explored deep cuts.

Source Action:
Search RYM for the artist's discography page — community rankings of each album.
Search AOTY for critic consensus.
Recommend the highest-rated gap album. If the gap album is a consensus weak entry, say so and pick the next-best gap.

Output:
1 release with: artist's overall play depth, which albums are well-covered vs missing, the specific gap album, critical context for its place in the discography.""",
    },
    "gap": {
        "name": "Gap Fill",
        "description": "Known canonical gaps from genre audits. Start with jumping-off album.",
        "spec": """\
Mode: gap_fill

Address known canonical gaps — foundational albums the user knows they're missing.

API Action:
1. /api/canonical to pull the canonical albums list. Filter by genre or tier as needed.
2. Cross-reference each canonical album against /api/artist/{name} or /api/album-completion?artist=[name] to check if the user has listened.
3. Albums with zero or near-zero plays are confirmed gaps. Pick the highest-tier unheard album.

Source Action:
Search the genre-specific publication (No Depression for alt-country, BrooklynVegan for punk crossover, etc.) for "essential" or "canon" lists.
Cross-reference with RYM genre charts and Rolling Stone genre lists.
Identify the single most important gap album with full historical context.

Jumping Off:
Always start with a familiar album: "You know [familiar album] well ([X] plays). [Gap album] is [specific connection]."

Output:
1 release with: the gap it fills, jumping-off connection, historical context (label, producer, scene, year), confirmed starting track.""",
    },
    "roots": {
        "name": "Root Tracing",
        "description": "Trace backward from modern favorites to genre foundations.",
        "spec": """\
Mode: root_tracing

Go backward from a genre the user loves to its foundational layer. Work forward chronologically from the root.

API Action:
Identify which era of a genre the user is heaviest in (e.g., 2000s alt-rock revival, modern outlaw country).
Query play counts for artists from earlier eras of the same genre to quantify the historical gap.
/api/canonical?genre=[genre] to find canonical root albums the user hasn't heard yet.

Source Action (uses the most sources):
1. Rolling Stone or Acclaimed Music genre retrospectives for canonical historical sequence
2. RYM genre charts filtered to the foundational decade
3. Genre-specific publication's "history of" or "beginner's guide" features
4. Pitchfork Sunday Reviews for older albums that influenced the user's favorites
Build a lineage map: [root artist] → [bridge artist] → [artist user already loves].

Output:
1 release from the root layer with: full lineage chain, historical context, era placement, confirmed starting track.""",
    },
    "scene": {
        "name": "Scene Exploration",
        "description": "Explore a city/label/era ecosystem as a network.",
        "spec": """\
Mode: scene_exploration

Lock onto a specific city/label/era ecosystem and explore it as a network — labels, venues, producers, not just artists.

Suggested scenes:
- Chicago post-rock (Thrill Jockey, Drag City, 1990s-2000s)
- Merge Records roster (Chapel Hill/Durham, indie rock institution)
- Bakersfield Sound (Buck Owens, Merle Haggard, 1950s-60s)
- Sinaloan corrido scene (Chalino's Culiacán through modern tumbados)
- 4AD Records (shoegaze/dream pop/ethereal, 1980s-90s UK)
- Dischord Records (DC hardcore/post-hardcore, Ian MacKaye's label)
- Pilsen/Chicago DIY (local to user)
- Muscle Shoals / FAME Studios (soul/R&B/country crossover, Alabama)
- Warp Records (electronic/IDM/experimental, Sheffield UK)

Source Action:
Discogs for label discography. Bandcamp Daily or genre-specific publication for scene reports. RYM for label pages and curated scene lists.
Map the ecosystem: key label(s), producer(s), venue(s), active years, defining aesthetic, 3-5 essential releases ranked by critical consensus.

Output:
Scene overview (3-5 sentences of infrastructure context) plus 1 entry-point release with connections to other scene releases for future sessions.""",
    },
    "bridge": {
        "name": "Bridge Building",
        "description": "Artists at the intersection of two genres you're into.",
        "spec": """\
Mode: bridge_building

Find artists at the intersection of two genres the user is already into. The interesting music lives in the overlaps.

API Action:
Identify the user's top 5 genres by total play count.
Look for artists that appear in RYM under descriptors from two of these genres simultaneously.

Source Action:
Search RYM for albums tagged with both genre descriptors.
Search Bandcamp Daily for cross-genre features.
Search for "[genre A] meets [genre B]" on Pitchfork or the relevant genre publication.
Prioritize albums that appear on lists for BOTH genres.

Intersection examples:
- Alt-country × post-rock (Calexico, Boxhead Ensemble)
- Corridos × punk (the Plugz, Piñata Protest)
- Slacker rock × folk (early Smog, Songs: Ohia)
- Hip-hop × jazz (Madlib side projects, Shabaka Hutchings crossover)
- Post-punk × Latin (Arto Lindsay, Mars)
- Electronic × country (Sturgill's Sound & Fury, Charley Crockett remixes)

Output:
1 release with: the two genres it bridges, how it sounds from each side, specific connections to artists the user already knows from each genre.""",
    },
    "adjacent": {
        "name": "Adjacent Genre",
        "description": "Entry point to an unexplored genre bordering your strong ones.",
        "spec": """\
Mode: adjacent_genre

Identify unexplored genres that border genres the user is deep in, and provide an entry point.

API Action:
Pull top-artists to identify 5 strongest genres via Last.fm tags.
/api/canonical to check which genres already have canonical entries — target genres with none.
Cross-reference to find genres with zero or near-zero plays that share sonic DNA.

Source Action:
Search Every Noise at Once or RYM genre map for adjacency relationships.
For the target genre, search AOTY, RYM, and Rolling Stone for the consensus #1 entry-point album.
Search the genre-specific publication for a "where to start" feature.
If 3 sources agree on the same entry point, that's the recommendation. If sources diverge, present top 2.

Adjacency logic:
- Strong in indie/alt-rock → post-rock, math rock, slowcore, power pop
- Strong in alt-country → Americana, Red Dirt, cosmic American music, folk
- Strong in corridos → Tejano, son jarocho, norteño deep cuts, narco-ballad history
- Strong in post-punk → darkwave, coldwave, industrial, no wave
- Strong in hip-hop → grime, UK drill, abstract hip-hop, spoken word/jazz poetry
- Strong in electronic → dub techno, Krautrock, ambient, musique concrète

Output:
Genre name, why it's adjacent to user's taste, consensus entry-point album, 1 confirmed track.""",
    },
    "mood": {
        "name": "Mood / Sonic",
        "description": "Cross-genre recs by texture/energy/emotion, not genre taxonomy.",
        "spec": """\
Mode: mood_sonic

Cross-genre recommendations based on texture, energy, and emotional register — not genre taxonomy.

API Action:
Pull /api/recent to understand current listening mood.
Identify sonic qualities: tempo, density, vocal style, production era, emotional register.

Sonic dimensions:
- Energy: catatonic → contemplative → steady → driving → explosive
- Density: sparse/skeletal → open → full → dense → wall-of-sound
- Warmth: cold/clinical → neutral → warm → lush → saturated
- Vocals: instrumental → buried/textural → conversational → melodic → operatic
- Production era: lo-fi/raw → vintage analog → clean digital → maximalist → futuristic

Source Action:
Search RYM by descriptors (not genres). Search Bandcamp tags. Use AOTY to find highly-rated albums matching those descriptors from genres the user hasn't explored.

Output:
1 release with: sonic profile match, mood fit rationale, genre-crossing connection ("this is from [genre] but shares [quality] with the [genre] you've been playing").""",
    },
    "cold": {
        "name": "Cold Discovery",
        "description": "Zero listens on the artist. Maximum distance, taste-rooted rationale.",
        "spec": """\
Mode: cold_discovery

Zero existing listens on the recommended artist. Maximum distance from the comfort zone, but with a rationale rooted in taste analysis.

API Action:
Pull top-artists and recent listening.
Identify taste patterns: what sonic qualities, lyrical themes, or production approaches recur across favorites regardless of genre.

Source Action (widest source net):
1. AOTY highest-rated current/recent year in genres with zero user plays
2. RYM genre charts for genres outside user's top 10
3. Bandcamp Daily for features on unfamiliar scenes
4. 1001 Albums list for entries from underrepresented genres/regions
5. Acclaimed Music for highly-ranked albums from genres with zero plays
Cross-reference: find an album with zero listens, multiple high source ratings, AND a connection to at least one quality in existing taste.

Jumping Off:
Even in cold discovery, start with a familiar album:
"You have zero listens in [genre], but you've played [familiar album] [X] times. That album's [specific quality] connects to [cold rec] because [concrete reason]."

Output:
1 release with: full context for why this specific listener would connect, jumping-off connection, source ratings, confirmed starting track.""",
    },
    "revisit": {
        "name": "Revisit",
        "description": "Albums loved then abandoned 6+ months ago.",
        "spec": """\
Mode: revisit

Albums the user listened to heavily in the past but hasn't played in 6+ months. Things that might hit differently now.

API Action:
Use /api/album-sessions?session_type=full for albums with completed listens.
Cross-reference /api/album-completion for total play depth.
Use /api/query with SQL:
SELECT raw_artist, raw_album, COUNT(*) as plays, MAX(listened_at) as last_played
FROM listen_events GROUP BY raw_artist, raw_album
HAVING COUNT(*) >= 10 AND MAX(listened_at) < NOW() - INTERVAL '6 months'
ORDER BY plays DESC.

Source Action:
Search for recent retrospective coverage, anniversary features, or reappraisals.
Check if the artist released new work since the user last listened — new context can reframe older albums.

Output:
1 release with: play history stats, time since last listen, what's changed since then, case for why now is a good time to return.""",
    },
    "live": {
        "name": "Live Shows",
        "description": "Chicago shows matched against listening history + discovery.",
        "spec": """\
Mode: live_shows

Find upcoming Chicago shows the user should attend based on listening history, discover new artists with upcoming local dates, track presales, and manage show interest.

API Action:
Primary: /api/chicago-shows/match with target date range (default: next 4 weeks, expandable to 3 months or full year). Returns shows ranked by relevance score (listen count × track breadth × recency × proximity) with artist listening stats included.

Supplement with:
- /api/chicago-shows/just-announced — check every session for newly listed shows (last 7 days)
- /api/chicago-shows/presales — presales starting within 14 days. Flag prominently for action.
- /api/chicago-shows?genre=[tag] — filter by Last.fm tag for genre browsing
- /api/discover?seed=[artist]&include_events=true — from interesting matches, find similar artists with upcoming Chicago dates (Tier 3 discovery)

Source Action:
For matched artists: /api/album-completion?artist=[name] for prep opportunities.
For discovery artists from /api/discover: search RYM/AOTY for best album. Use the discover endpoint's genre tags and similarity scores for context.

Interest Tracking:
PUT /api/chicago-shows/interest with show_id + status (going|interested|not_interested|cant_afford) + optional note.
GET /api/chicago-shows/interest to review tracked shows at session start.
GET /api/chicago-shows/interest?status=interested for shows the user is on the fence about.

Day Priority:
Friday/Saturday first — present in full.
Weekday (Mon-Thu) only if: Tier 1 artist (50+ plays), rare/one-off appearance, or presale/ticket urgency.
Sunday: Tier 1 + strong Tier 2.
Group by weekend first, then significant weekday shows.

Output per show:
Day of week, date, venue, artist(s), time, ticket link.
Travel: always include venue distance — driving and transit minutes from the match response's travel object. Format as "~Xmin drive / ~Ymin transit". Helps gauge whether a weekday show is feasible or if the commute makes it a pass. If travel data is missing, note "distance unknown".
Relevance context: listen count, top albums, last listen date.
Prep recommendation: specific album to listen to before the show.
Multi-artist bills: note which artists user knows vs doesn't.
Flag [JUST ANNOUNCED], [PRESALE SOON], [THALIA HALL] where applicable.
After presenting matches, offer discovery exploration via /api/discover.""",
    },
    "festival": {
        "name": "Festival Scout",
        "description": "Festival lineup analysis, prep plans, undercard discovery.",
        "spec": """\
Mode: festival_scout

Analyze Chicago festival lineups against listening history, identify must-see acts and discovery opportunities, build a prep plan.

API Action:
Primary: /api/chicago-shows/match?festival=[name] for lineup cross-reference with relevance scores. Fuzzy matching — "lolla", "riot", "arc" all work.
Full lineup: /api/chicago-shows?festival=[name]&limit=1000.
Discovery: Pick 3-5 highest-matched artists, run /api/discover?seed=[artist]&include_events=true to find similar artists on the same lineup.
Unannounced lineups: Fall back to web search "[festival] [year] lineup" then /api/artists/batch.

Source Action:
For discovery artists: search RYM/AOTY for best album.
For unannounced festivals: search for announcement dates and presale info.
Partial lineups: present what's known, flag when full lineup expected.

Interest Tracking:
Track interest per show/artist — helps decide which day pass to buy.

Key Chicago Festivals:
Lollapalooza (late Jul/Aug), Riot Fest (mid-Sept), ARC (early Sept), Sueños (May), Pitchfork (check status), Chicago Blues Fest (June, free), Chicago Jazz Fest (Labor Day, free), Chicago House Fest (late Aug, free), World Music Fest (late Sept, free), Windy City Smokeout (July, country), Summer Smash (June, hip-hop), Beyond Wonderland (June, EDM), Ravinia (summer-long), CIVL Fest (April), Tomorrow Never Knows (January).

Output:
Matched artists by relevance score with listen counts and top albums.
Travel: include venue distance — driving and transit minutes from the match response's travel object. Format as "~Xmin drive / ~Ymin transit". Helps gauge whether a weekday show is feasible or if the commute makes it a pass.
5-10 discovery picks via /api/discover with genre context and one prep album each.
Overall score: "X artists you already love, Y started exploring, Z discovery candidates."
Ordered prep playlist: matched artists with incomplete albums first, then discovery entry points.
Set time conflicts if daily schedule available.""",
    },
    "canon-builder": {
        "name": "Canon Builder",
        "description": "Build and curate the canonical albums reference table for a genre.",
        "spec": """\
Mode: Canon Builder
ID: canon-builder

HARD RULES — read these first, they override everything below:
1. Do NOT call any listening history endpoints. No /api/top-artists, /api/top-albums, \
/api/recent, /api/artist/, /api/summary, /api/session-start, or /api/album-completion. \
The user's listening data is IRRELEVANT to building a canon. The only API calls you \
make are GET /api/canonical/gaps (to check what's already in the table) and \
POST /api/canonical (to write the results).
2. Do NOT create React components, artifacts, interactive UIs, tables, or any visual \
rendering. No artifacts of any kind. Your output is plain text and API calls. Nothing else.
3. Do NOT ask the user for approval, confirmation, or input. This mode is fully \
autonomous. Build the list, push it, report what you did.

Purpose: Build the canonical albums reference table for a genre using critical \
consensus. This is the only mode where the primary output is writing to the database, \
not giving a listening recommendation.

Workflow:
1. User specifies a genre (and optionally subgenre).
2. Check what's already in the table: GET /api/canonical/gaps?genre={genre}
   - If populated: note what exists, avoid duplicates.
   - If empty: start fresh.
3. From your own knowledge of critical consensus (RYM, AOTY, Pitchfork, Rolling Stone, \
genre-specific histories, Quietus, Bandcamp Daily), build the canon in three tiers:
   - Essential: Consensus classics. Roughly 10-20 albums.
   - Important: Influential, critically acclaimed, historically significant. Roughly 15-30.
   - Deep: Cult favorites, scene-specific landmarks. Roughly 10-20.
   These counts are loose guides, not targets. A massive genre like jazz or \
hip-hop will naturally have more essentials than a niche subgenre like slowcore. \
Scale up for big genres, and never pad a small genre to hit a number.
4. For each album: artist, title, year, one-line description of why it belongs.
5. For borderline picks, make the call yourself. If 2+ credible sources include it, \
it's in. If only one source and it's not genre-defining, leave it out. Note your \
reasoning briefly in the description field for anything that was a close call.
6. Immediately batch-write everything: POST /api/canonical with the full list. \
Do not wait for user review. Do not ask permission. Just push it.
7. After the write, show the user a plain text summary: tier counts, any close \
calls noted, and any albums that bridge genres.

Source philosophy:
- Use multiple critical perspectives. Never rely on a single source.
- Don't pad the list. If a genre's canon is 25 albums deep, stop at 25. If it's 60, \
go to 60.
- Note when an album bridges two genres — user may have it filed elsewhere.
- Note in the description field when a pick is contentious.

What this mode does NOT do:
- Does NOT read the user's listening history. Not for building the canon, not for \
context, not for anything. The canon is defined by the world, not by one listener.
- Does NOT generate artifacts, React components, or visual UIs of any kind.
- Does NOT ask for user input or confirmation mid-workflow.
- Does NOT recommend non-canonical albums or suggest listening order.

Relationship to other modes:
- Canon Builder populates the canonical_albums table.
- Gap Fill, Deep Discography, and other modes READ from it via /api/canonical/gaps \
to give better recommendations.
- After a Canon Builder session, all other modes become more useful for that genre.""",
    },
}
