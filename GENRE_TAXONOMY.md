# Canon Builder — Genre Taxonomy v3

## Design Principles

1. **Two-level hierarchy**: `genre` (controlled vocabulary) + `subgenre` (free-text, optional)
2. **Genre is the canonical key**: every canon builder run maps to exactly ONE genre value
3. **Title Case everywhere**: no more `jazz` vs `Hip-Hop` casing mismatches
4. **No slashes in genre field**: slashes were doing three different things — stop that
5. **Subgenre is descriptive, not structural**: subgenre adds color but genre is what you query against
6. **One album, one genre**: the UNIQUE(artist, album) constraint means each album lives in exactly one genre. Pick the best home.

---

## Controlled Genre Vocabulary

122 genres organized by family. The **Family** column is for human navigation only — it is NOT stored in the database. Only **Genre** goes in the `genre` column.

### Hip-Hop
| Genre | Notes |
|---|---|
| East Coast Hip-Hop | Boom-bap, conscious, NYC lineage |
| West Coast Hip-Hop | G-Funk, gangsta, Bay Area |
| Southern Hip-Hop | OutKast, UGK, Three 6, crunk, bounce |
| Underground Hip-Hop | Abstract, art rap, DOOM, Billy Woods |
| Trap | Atlanta trap, drill, modern mainstream |
| SoundCloud Rap | Lil Peep, X, Carti era |
| French Hip-Hop | MC Solaar, IAM, PNL |
| Latin Hip-Hop | Spanish-language rap, Latin trap |
| Japanese Hip-Hop | Nujabes, KOHH, Awich |
| Korean Hip-Hop | Epik High, Dynamic Duo, Zico |

### Rock — Classic Era
| Genre | Notes |
|---|---|
| Classic Rock | 60s-80s rock spectrum |
| Psychedelic Rock | Acid rock, psych-folk, neo-psych |
| Progressive Rock | Canterbury, symphonic, kraut-adjacent prog |
| Blues | Delta, Chicago, Texas, all subgenres via subgenre field |
| Garage Rock | Proto-punk, 60s garage, garage revival |

### Rock — Punk Lineage
| Genre | Notes |
|---|---|
| Punk | 77 punk, hardcore, anarcho-punk, crust |
| Post-Punk | Joy Division through Fontaines D.C. |
| Goth | Goth rock, darkwave, deathrock, coldwave, ethereal wave |
| Emo | Midwest emo, DC emo, emo revival, third wave |
| Screamo | Screamo, post-hardcore, mathcore crossover |
| Post-Hardcore | At the Drive-In, Fugazi, Glassjaw |
| Grunge | Seattle sound, proto-grunge |
| Pop Punk | Skate punk, easycore, pop-punk/emo crossover |

### Rock — Alternative & Indie
| Genre | Notes |
|---|---|
| Indie Rock | Pixies through modern indie |
| Britpop | Madchester, baggy, Britpop proper |
| Art Rock | Talking Heads, Bowie art era, St. Vincent |
| Art Pop | Kate Bush, Bjork, Weyes Blood |
| Lo-Fi Rock | Slacker rock, GBV, Pavement, early Beck |
| Bedroom Pop | Clairo, Boy Pablo, mk.gee |
| Post-Rock | Slint, Mogwai, GY!BE, Tortoise |
| Shoegaze | Shoegaze proper + dream pop (heavy overlap) |
| Math Rock | Don Cab, Battles, toe, TTNG |
| Power Pop | Big Star, Cheap Trick, jangle pop, R.E.M. early |
| Noise Rock | Swans, Big Black, Jesus Lizard + No Wave |

### Rock — Heavy
| Genre | Notes |
|---|---|
| Classic Metal | Sabbath, Priest, Maiden, trad metal |
| NWOBHM | Iron Maiden, Diamond Head, Saxon — split from Classic Metal |
| Thrash Metal | Big 4, Kreator, Exodus, crossover thrash |
| Death Metal | Florida, Swedish, tech-death, brutal, progressive |
| Black Metal | Norwegian, atmospheric, DSBM, war metal |
| Doom Metal | Doom, sludge, stoner, drone, death-doom |
| Progressive Metal | Tool, Opeth, Mastodon, djent |
| Post-Metal | Isis, Neurosis, Cult of Luna, Deafheaven |
| Metalcore | Converge, Botch, Norma Jean, melodic metalcore |

### Industrial
| Genre | Notes |
|---|---|
| Industrial | TG, NIN, Ministry, EBM, industrial metal |

### Electronic
| Genre | Notes |
|---|---|
| House | Chicago, deep, acid, French, UK, minimal, lo-fi |
| Techno | Detroit, Berlin, dub techno, industrial techno |
| Drum And Bass | Jungle, liquid, neurofunk, darkstep |
| UK Bass | UK garage, grime, dubstep, post-dubstep |
| Ambient | Eno lineage, dark ambient, ambient pop |
| IDM | Autechre, Aphex, Boards of Canada, experimental electronic |
| Krautrock | Can, Neu!, Faust, kosmische, motorik |
| Disco | Donna Summer, Chic, Moroder, Italo disco |
| Footwork | Juke, RP Boo, DJ Rashad, Chicago footwork |
| Trance | Progressive trance, goa, psytrance, eurodance |
| Trip-Hop | Massive Attack, Portishead, DJ Shadow, downtempo |
| Japanese Electronic | Onkyokei, Japanese noise (Merzbow), YMO lineage |

### Electronic — Pop-Adjacent
| Genre | Notes |
|---|---|
| New Wave | Synth-pop, new romantic, post-punk pop crossover |
| Vaporwave | Future funk, mallsoft, signalwave |
| Hyperpop | PC Music, 100 gecs, charli xcx glitch era |
| Phonk | Drift phonk, Memphis revival, cowbell phonk |

### Soul, R&B, Funk
| Genre | Notes |
|---|---|
| Classic Soul | Motown, Stax, Philly soul, northern soul |
| Contemporary R&B | Modern R&B, neo-soul, alt-R&B, PBR&B |
| Funk | Parliament, James Brown, Sly Stone, boogie |
| Quiet Storm | Luther Vandross, Anita Baker, Sade |

### Country & Folk
| Genre | Notes |
|---|---|
| Traditional Country | Hank, Patsy, Merle, honky-tonk, Nashville sound |
| Outlaw Country | Waylon, Willie, Kristofferson, Townes |
| Country Rock | Cosmic country, California country rock, Gram Parsons |
| Americana | Alt-country, roots rock, gothic Americana, modern Americana |
| Bluegrass | Monroe through Punch Brothers, progressive bluegrass |
| Traditional Folk | Revival folk, protest folk, British folk |
| Singer-Songwriter | Modern indie folk, confessional, Bon Iver through Phoebe Bridgers |

### Jazz
| Genre | Notes |
|---|---|
| Early Jazz | Swing, big band, New Orleans, stride |
| Bebop | Hard bop, cool jazz, West Coast jazz |
| Modal Jazz | Post-bop, spiritual jazz, Coltrane through Kamasi |
| Free Jazz | Avant-garde jazz, loft jazz, free improv |
| Jazz Fusion | Jazz-rock, ECM school, electric Miles |
| Modern Jazz | Contemporary jazz, London jazz scene, nu-jazz |
| Japanese Jazz | Jazz kissa canon, Ryo Fukui, Soil & Pimp |
| Latin Jazz | Boogaloo, Afro-Cuban jazz, Cal Tjader, Tito Puente |

### Latin
| Genre | Notes |
|---|---|
| Regional Mexican | Norteno, banda, ranchera, mariachi |
| Corridos Tumbados | Movimiento alterado, Fuerza Regida, Peso Pluma |
| Cumbia | Colombian, Mexican, Argentine digital cumbia |
| Salsa | Son cubano, salsa dura, salsa romantica, tropical |
| Latin Pop | Shakira, Juanes, Spanish-language pop |
| Reggaeton | Perreo, dembow, Daddy Yankee through Bad Bunny |
| Rock En Espanol | Soda Stereo, Cafe Tacvba, Caifanes, Mana |
| Nueva Cancion | Nueva trova, Violeta Parra, Victor Jara, Silvio |
| Bolero | Agustin Lara, Los Panchos, Luis Miguel bolero era |

### Brazilian
| Genre | Notes |
|---|---|
| Bossa Nova | Jobim, Gilberto, cool samba-jazz fusion |
| Samba | Traditional samba, pagode, samba-rock |
| Tropicalia | Os Mutantes, Gil, Veloso, tropicalist movement |
| MPB | Musica Popular Brasileira, post-tropicalia pop |
| Sertanejo | Brazilian country, sertanejo universitario |
| Baile Funk | Brazilian funk, MC Kevinho, Anitta crossover |

### African
| Genre | Notes |
|---|---|
| Afrobeat | Fela Kuti lineage, highlife, juju — West African roots |
| Afrobeats | Modern Afropop, Burna Boy, Wizkid, amapiano-adjacent |
| Ethiopian Jazz | Ethio-groove, Mulatu Astatke, Mahmoud Ahmed |

### Caribbean
| Genre | Notes |
|---|---|
| Reggae | Roots reggae, dub, lovers rock |
| Dancehall | Digital dancehall, ragga, modern dancehall |

### Japanese
| Genre | Notes |
|---|---|
| City Pop | Tatsuro Yamashita, Mariya Takeuchi, 80s Japanese pop |
| J-Rock | Japanese alternative, Japanese indie, shibuya-kei overlap |
| Shibuya-Kei | Pizzicato Five, Cornelius, Flipper's Guitar |
| Visual Kei | X Japan, Dir En Grey, Malice Mizer |
| Enka | Traditional Japanese popular ballad |
| Japanese Noise | Boris, Melt-Banana, Boredoms, hardcore/noise |
| Anison | Anime OST, anime songs, Yoko Kanno |

### Korean
| Genre | Notes |
|---|---|
| K-Pop | Idol pop, K-pop groups, SM/YG/JYP/HYBE lineage |
| K-Indie | Korean indie rock, K-R&B, The Black Skirts, Hyukoh |
| Trot | Foundational Korean pop form |

### French
| Genre | Notes |
|---|---|
| Chanson | Variete francaise, ye-ye, Piaf through Gainsbourg |
| French Touch | French house, Daft Punk, Justice, Air |
| French Pop | Nouvelle chanson, modern French pop, Christine and the Queens |

### Pop
| Genre | Notes |
|---|---|
| Classic Pop | Pre-MTV, Motown-pop crossover, 60s-80s pop |
| Modern Pop | 2000s-present, Max Martin era, streaming-era pop |

### Classical & Composition
| Genre | Notes |
|---|---|
| Orchestral | Symphonic, concerto, Romantic era, Baroque |
| Chamber Music | Piano, solo instrument, string quartet |
| Modern Classical | 20th century, minimalism, contemporary composition |
| Film Score | Soundtrack, John Williams through Jonny Greenwood |
| Video Game OST | Nobuo Uematsu, Koji Kondo, Undertale, Celeste |

### Other
| Genre | Notes |
|---|---|
| Ska | 2-Tone, ska punk, first/second/third wave |
| Gospel | Spiritual, sacred music, gospel choir |
| Musical Theater | Broadway, West End, Sondheim through Lin-Manuel |

---

## Total: 122 genres
