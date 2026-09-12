"""Mock catalog: 48 fictional tracks with the same attributes we'd get from
Spotify, so the DJ brain behaves identically in demo and live mode.
Names are invented; attributes are plausible for the genre."""

# (id, title, artist, year, duration_s, genres, energy, dance, valence, tempo, popularity)
_RAW = [
    ("m01", "Neon Corridor", "Vela Nights", 2023, 198, ["pop", "dance pop"], .84, .88, .75, 124, 82),
    ("m02", "Glass Hearts", "Juno Reyes", 2022, 212, ["pop"], .62, .70, .55, 98, 76),
    ("m03", "Run It Back", "KILO & Marrow", 2024, 176, ["hip hop", "trap"], .78, .82, .60, 140, 88),
    ("m04", "Slow Orbit", "Halcyon Drift", 2021, 245, ["r&b", "chill"], .38, .55, .40, 82, 61),
    ("m05", "Saturday Static", "The Paper Kites Club", 2019, 203, ["indie pop", "indie rock"], .72, .66, .80, 118, 70),
    ("m06", "Midnight Merengue", "Sol y Cuerda", 2020, 191, ["latin", "reggaeton"], .86, .92, .85, 96, 84),
    ("m07", "Bassline Theory", "DRVN", 2023, 220, ["house", "edm"], .93, .90, .60, 126, 79),
    ("m08", "Porchlight", "Ada Lowell", 2018, 232, ["folk", "acoustic", "chill"], .30, .42, .62, 74, 52),
    ("m09", "Hollow Crown", "Ironvale", 2017, 256, ["rock", "alt rock"], .88, .48, .35, 152, 66),
    ("m10", "Cherry Soda Summer", "Bubblegum Arcade", 2001, 187, ["pop", "2000s"], .77, .80, .90, 116, 73),
    ("m11", "Dial-Up Love", "Frankie 56k", 2003, 224, ["r&b", "2000s"], .58, .74, .70, 92, 68),
    ("m12", "Two Step Tuesday", "Big Mo & The Block", 2006, 240, ["hip hop", "2000s", "crunk"], .81, .86, .72, 76, 75),
    ("m13", "Disco Elevator", "Mirrorball Society", 1978, 268, ["disco", "funk", "70s"], .79, .91, .88, 118, 64),
    ("m14", "Cassette Sunset", "Vanta Beach", 1986, 236, ["synth pop", "80s"], .70, .76, .74, 112, 62),
    ("m15", "Landline", "The Cul-de-Sacs", 1994, 214, ["alt rock", "grunge", "90s"], .82, .50, .45, 132, 59),
    ("m16", "Boulevard Bounce", "Lil Ferris", 1997, 229, ["hip hop", "90s", "g-funk"], .68, .84, .78, 94, 71),
    ("m17", "Afterparty Arithmetic", "Count Zero", 2024, 168, ["hip hop", "trap"], .74, .85, .52, 150, 80),
    ("m18", "Coastline", "Marina Okoye", 2022, 208, ["afrobeats", "r&b"], .71, .87, .82, 104, 83),
    ("m19", "Lagos to LA", "Tobi Wave", 2023, 195, ["afrobeats", "pop"], .80, .90, .86, 108, 85),
    ("m20", "Fever Pitch", "Stadium Ghosts", 2020, 201, ["rock", "pop punk"], .91, .55, .68, 168, 69),
    ("m21", "Velvet Room", "Séance", 2021, 238, ["r&b", "soul"], .45, .62, .48, 88, 63),
    ("m22", "Tequila Mathematics", "Los Vectores", 2019, 184, ["latin", "cumbia"], .83, .89, .90, 100, 72),
    ("m23", "Ctrl+Alt+Dance", "Byte Sized", 2022, 178, ["hyperpop", "electronic"], .95, .84, .70, 160, 58),
    ("m24", "Low Tide", "Halcyon Drift", 2023, 266, ["chill", "lo-fi", "electronic"], .28, .50, .45, 78, 55),
    ("m25", "Golden Hour Drive", "Juno Reyes", 2024, 216, ["pop", "indie pop"], .66, .72, .78, 110, 81),
    ("m26", "Sirens on Sixth", "Vela Nights", 2021, 205, ["dance pop", "electronic"], .88, .91, .58, 128, 74),
    ("m27", "Homecoming", "Ada Lowell", 2020, 249, ["folk", "country"], .42, .48, .70, 86, 57),
    ("m28", "Trapdoor", "KILO & Marrow", 2023, 182, ["trap", "hip hop"], .85, .80, .40, 145, 77),
    ("m29", "Boombox Prophet", "Big Mo & The Block", 2004, 233, ["hip hop", "2000s"], .76, .83, .66, 90, 70),
    ("m30", "Skate Park Anthem", "The Cul-de-Sacs", 1999, 197, ["pop punk", "90s"], .90, .58, .74, 176, 65),
    ("m31", "Cumbia at Dawn", "Sol y Cuerda", 2022, 210, ["latin", "cumbia", "chill"], .55, .78, .80, 96, 60),
    ("m32", "Warehouse Weather", "DRVN", 2024, 251, ["techno", "edm"], .96, .86, .40, 134, 71),
    ("m33", "Rooftop Reception", "Mirrorball Society", 1981, 244, ["disco", "80s", "funk"], .74, .88, .84, 120, 61),
    ("m34", "Country Club Dropout", "Ironvale", 2022, 215, ["rock", "indie rock"], .80, .52, .60, 140, 63),
    ("m35", "Sugar Rush Hour", "Bubblegum Arcade", 2005, 192, ["pop", "2000s", "dance pop"], .85, .85, .92, 122, 74),
    ("m36", "Late Checkout", "Marina Okoye", 2024, 226, ["r&b", "afrobeats"], .52, .70, .58, 94, 79),
    ("m37", "Payphone Confessions", "Frankie 56k", 2001, 218, ["r&b", "2000s", "soul"], .50, .68, .52, 84, 66),
    ("m38", "Spring Break Forever", "Stadium Ghosts", 2023, 189, ["pop punk", "pop"], .89, .64, .81, 172, 68),
    ("m39", "Anthem for the Uber Home", "Count Zero", 2022, 204, ["hip hop", "chill"], .48, .66, .44, 82, 69),
    ("m40", "Confetti Cannon", "Byte Sized", 2024, 162, ["hyperpop", "dance pop"], .97, .89, .88, 150, 62),
    ("m41", "Sunday Reset", "Halcyon Drift", 2020, 271, ["lo-fi", "chill"], .22, .44, .50, 72, 54),
    ("m42", "Dembow Daydream", "Tobi Wave & Los Vectores", 2024, 186, ["reggaeton", "afrobeats"], .84, .93, .83, 98, 86),
    ("m43", "Pixel Prom", "Vanta Beach", 1988, 228, ["synth pop", "80s", "pop"], .73, .79, .79, 118, 60),
    ("m44", "Mosh Pit Etiquette", "Ironvale", 2019, 174, ["metal", "rock"], .98, .40, .30, 190, 49),
    ("m45", "Quiet Car", "Séance", 2023, 258, ["soul", "chill", "jazz"], .33, .46, .42, 76, 58),
    ("m46", "Bodega Champagne", "Lil Ferris", 1996, 236, ["hip hop", "90s"], .66, .82, .75, 92, 67),
    ("m47", "Overtime", "Marrow", 2024, 179, ["hip hop", "drill"], .82, .78, .38, 142, 78),
    ("m48", "Last Song at the House Party", "Juno Reyes", 2023, 241, ["pop", "indie pop", "chill"], .54, .60, .66, 100, 77),
]

CATALOG = [
    {
        "id": f"mock:{i}", "title": t, "artist": a, "album": None, "year": y, "duration_ms": d * 1000,
        "genres": g, "energy": e, "danceability": da, "valence": v, "tempo": float(tp), "popularity": p,
        "art_url": None, "preview_url": None, "explicit": 0,
    }
    for (i, t, a, y, d, g, e, da, v, tp, p) in _RAW
]
BY_ID = {c["id"]: c for c in CATALOG}


def search(q, limit=12):
    q = q.lower().strip()
    if not q:
        return CATALOG[:limit]
    words = q.split()

    def score(c):
        hay = f"{c['title']} {c['artist']} {' '.join(c['genres'])} {c['year']}".lower()
        s = sum(3 if w in c["title"].lower() else 2 if w in c["artist"].lower() else 1 if w in hay else 0 for w in words)
        return s + c["popularity"] / 1000
    ranked = [c for c in CATALOG if score(c) >= 1]
    ranked.sort(key=score, reverse=True)
    return ranked[:limit]
