"""A short, curated list of REAL, currently popular songs with verified
Spotify track URIs -- for testing playback without ever calling /search."""

REAL_TRACKS = [
    ("spotify:track:0VjIjW4GlUZAMYd2vXMi3b", "Blinding Lights", "The Weeknd", 2020, 200,
     ["synth pop", "dance pop"], .73, .51, .33, 171, 90),
    ("spotify:track:0yLdNVWF3Srea0uzk55zFn", "Flowers", "Miley Cyrus", 2023, 200,
     ["pop"], .68, .71, .65, 118, 88),
    ("spotify:track:2plbrEY59IikOBgBGLjaoe", "Die With A Smile", "Lady Gaga, Bruno Mars", 2024, 251,
     ["pop"], .54, .52, .53, 158, 92),
    ("spotify:track:1u8c2t2Cy7UBoG4ArRcF5g", "Blank Space", "Taylor Swift", 2014, 231,
     ["pop", "2010s"], .75, .76, .57, 96, 85),
    ("spotify:track:4PTG3Z6ehGkBFwjybzWkR8", "Never Gonna Give You Up", "Rick Astley", 1987, 213,
     ["synth pop", "80s"], .77, .77, .96, 113, 78),
    ("spotify:track:003vvx7Niy0yvhvHt4a68B", "Mr. Brightside", "The Killers", 2004, 222,
     ["rock", "alt rock", "2000s"], .90, .36, .24, 148, 87),
    ("spotify:track:7qiZfU4dY1lWllzX7mPBI3", "Shape of You", "Ed Sheeran", 2017, 234,
     ["pop"], .65, .83, .93, 96, 88),
    ("spotify:track:2374M0fQpWi3dLnB54qaLX", "Africa", "TOTO", 1982, 295,
     ["rock", "80s"], .62, .55, .70, 92, 82),
    ("spotify:track:4Dvkj6JhhA12EX05fT7y2e", "As It Was", "Harry Styles", 2022, 167,
     ["pop"], .52, .52, .69, 174, 89),
    ("spotify:track:5g7sDjBhZ4I3gcFIpkrLuI", "Locked out of Heaven", "Bruno Mars", 2012, 233,
     ["pop", "funk"], .77, .73, .90, 144, 82),
    ("spotify:track:6dOtVTDdiauQNBQEDOtlAB", "BIRDS OF A FEATHER", "Billie Eilish", 2024, 210,
     ["pop"], .51, .75, .74, 105, 90),
    ("spotify:track:0d28khcov6AiegSCpG5TuT", "Feel Good Inc", "Gorillaz", 2005, 222,
     ["alt rock", "hip hop", "2000s"], .80, .82, .58, 138, 84),
    ("spotify:track:6habFhsOp2NvshLv26DqMb", "Despacito", "Luis Fonsi, Daddy Yankee", 2017, 227,
     ["latin", "reggaeton"], .82, .66, .85, 178, 86),
    ("spotify:track:4kLLWz7srcuLKA7Et40PQR", "I Gotta Feeling", "Black Eyed Peas", 2009, 289,
     ["dance pop", "2000s"], .74, .68, .66, 128, 80),
    ("spotify:track:7lQ8MOhq6IN2w8EYcFNSUk", "Without Me", "Eminem", 2002, 291,
     ["hip hop", "2000s"], .81, .82, .68, 112, 81),
    ("spotify:track:3n3Ppam7vgaVa1iaRUc9Lp", "Mr. Blue Sky", "Electric Light Orchestra", 1977, 303,
     ["rock", "70s"], .70, .43, .73, 175, 74),
    ("spotify:track:32OlwWuMpZ6b0aN2RZOeMS", "Uptown Funk", "Mark Ronson ft. Bruno Mars", 2014, 270,
     ["funk", "pop"], .86, .86, .93, 115, 85),
]


def seed_real_tracks(party, upsert_track_fn, db):
    out = []
    for uri, title, artist, year, dur, genres, energy, dance, valence, tempo, pop in REAL_TRACKS:
        t = {
            "id": uri, "title": title, "artist": artist, "album": None, "year": year,
            "duration_ms": dur * 1000, "genres": genres, "energy": energy, "danceability": dance,
            "valence": valence, "tempo": float(tempo), "popularity": pop,
            "art_url": None, "preview_url": None, "explicit": 0,
        }
        upsert_track_fn(t)
        out.append(t)
    db.commit()
    return out


def seed_and_suggest(party, guest_id, upsert_track_fn, db):
    """Seed the tracks AND queue them as suggestions attributed to guest_id
    (typically the host), so autopilot/Skip can play them immediately with
    zero /search calls. Returns (added, total)."""
    tracks = seed_real_tracks(party, upsert_track_fn, db)
    added = 0
    for t in tracks:
        try:
            db.execute("INSERT INTO suggestions (party_id, track_id, guest_id, note) VALUES (?,?,?,?)",
                       (party["id"], t["id"], guest_id, "test track"))
            db.execute("INSERT OR REPLACE INTO suggestion_votes (party_id, track_id, guest_id, value) VALUES (?,?,?,1)",
                       (party["id"], t["id"], guest_id))
            added += 1
        except Exception:
            pass
    db.commit()
    return added, len(tracks)
