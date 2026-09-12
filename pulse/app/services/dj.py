"""The DJ brain. Pure functions over plain dicts so it's unit-testable and
explainable: every pick returns the score AND the components that made it.

score(track) =
    demand      how many different guests asked for it (log-scaled)
  + momentum    net +1/-1 votes on the queued suggestion
  + theme       overlap with the active theme's keywords / decade / vibe
  + energy      how close the track's energy is to the party's current target
  + affinity    what the room has been voting for, by genre, learned live
  + freshness   small bonus for newer suggestions so the queue keeps moving
  - repeat      same artist as one of the last few plays
  - replay      already played recently (near-veto)
  - explicit    optional host setting
"""
import math
import re
from datetime import datetime, timezone

NOW_YEAR = datetime.now().year

# Words that map to an energy target rather than a genre.
VIBE_WORDS = {
    "chill": .35, "mellow": .35, "slow": .35, "wind down": .35, "late night": .4, "lowkey": .4, "acoustic": .35,
    "vibe": .55, "vibes": .55, "groove": .65, "dance": .8, "dancing": .8, "hype": .9, "turn up": .9, "turnt": .9,
    "banger": .9, "bangers": .9, "rage": .95, "mosh": .95, "party": .8, "pregame": .75, "workout": .85,
}
# Genre aliases so "hiphop", "hip-hop", "rap" all hit the same tag.
GENRE_ALIASES = {
    "hiphop": "hip hop", "hip-hop": "hip hop", "rap": "hip hop", "edm": "edm", "electronic": "electronic",
    "house": "house", "techno": "techno", "latin": "latin", "latino": "latin", "reggaeton": "reggaeton",
    "afrobeat": "afrobeats", "afrobeats": "afrobeats", "rnb": "r&b", "r&b": "r&b", "rock": "rock",
    "punk": "pop punk", "pop": "pop", "indie": "indie pop", "country": "country", "folk": "folk", "disco": "disco",
    "funk": "funk", "soul": "soul", "jazz": "jazz", "metal": "metal", "trap": "trap", "drill": "drill",
    "cumbia": "cumbia", "synth": "synth pop", "synthwave": "synth pop", "lofi": "lo-fi", "lo-fi": "lo-fi",
    "hyperpop": "hyperpop", "kpop": "k-pop", "k-pop": "k-pop",
}
STOP = {"the", "a", "an", "and", "or", "of", "only", "night", "songs", "music", "hits", "hour", "time", "mode", "please", "some", "more"}


def parse_theme(text):
    """'2000s throwback bangers' -> keywords, year range, target energy."""
    t = text.lower()
    year_from = year_to = None
    m = re.search(r"\b(19|20)?(\d0)s\b", t)                # 80s, 1990s, 2000s
    if m:
        century, dec = m.group(1), int(m.group(2))
        if century:
            year_from = int(century) * 100 + dec
        else:
            year_from = (2000 if dec <= 20 else 1900) + dec
        year_to = year_from + 9
    if "throwback" in t and not year_from:
        year_from, year_to = 1990, NOW_YEAR - 12
    if "oldies" in t and not year_from:
        year_from, year_to = 1950, 1989
    if re.search(r"\b(new|latest|current|2025|2026)\b", t) and not year_from:
        year_from, year_to = NOW_YEAR - 2, NOW_YEAR

    energy = None
    for w, e in VIBE_WORDS.items():
        if w in t:
            energy = e if energy is None else (energy + e) / 2
    keywords = set()
    for w in re.findall(r"[a-z&\-]+", t):
        if w in GENRE_ALIASES:
            keywords.add(GENRE_ALIASES[w])
        elif w not in STOP and w not in VIBE_WORDS and len(w) > 2 and not re.fullmatch(r"\d+s?", w):
            keywords.add(w)
    for phrase in ("hip hop", "pop punk", "dance pop", "synth pop", "indie pop", "indie rock", "alt rock"):
        if phrase in t:
            keywords.add(phrase)
    return {"keywords": sorted(keywords), "year_from": year_from, "year_to": year_to, "target_energy": energy}


def energy_arc(elapsed_min, total_min=None):
    """Target energy over the party. Warm up -> peak -> wind down."""
    if total_min and total_min > 0:
        f = min(1.0, max(0.0, elapsed_min / total_min))
    else:
        f = min(1.0, elapsed_min / 240.0)     # assume ~4h if the host set no end time
    if f < 0.15:
        return 0.55 + f / 0.15 * 0.15          # 0.55 -> 0.70
    if f < 0.35:
        return 0.70 + (f - 0.15) / 0.20 * 0.15  # -> 0.85
    if f < 0.80:
        return 0.85
    return 0.85 - (f - 0.80) / 0.20 * 0.35     # -> 0.50


def genre_affinity(recent_plays):
    """recent_plays: [{genres:[...], up:int, down:int}] -> {genre: affinity in -1..1}.
    Shrunk toward 0 so one lucky song doesn't hijack the night."""
    acc = {}
    for p in recent_plays:
        n = p["up"] + p["down"]
        if n == 0:
            continue
        net = p["up"] - p["down"]
        for g in p["genres"]:
            a = acc.setdefault(g, [0, 0])
            a[0] += net
            a[1] += n
    return {g: net / (n + 3) for g, (net, n) in acc.items()}


def crowd_energy_adjust(recent_plays):
    """If high-energy songs are getting buried and low-energy ones loved (or vice
    versa), nudge the target. Returns delta in [-0.15, 0.15]."""
    num = den = 0.0
    for p in recent_plays[-6:]:
        n = p["up"] + p["down"]
        if n == 0 or p.get("energy") is None:
            continue
        ratio = (p["up"] - p["down"]) / n            # -1..1
        num += ratio * (p["energy"] - 0.6) * n        # liked & high -> positive
        den += n
    if den == 0:
        return 0.0
    return max(-0.15, min(0.15, num / den))


WEIGHTS = {
    "demand": 3.0, "momentum": 1.2, "theme": 2.5, "energy": 2.0, "affinity": 1.5,
    "freshness": 0.5, "repeat": -2.0, "replay": -8.0, "explicit": -3.0, "popularity": 0.3,
}


def score_candidate(track, ctx):
    """track: dict with genres/energy/year/artist/title(+popularity, explicit).
    ctx: {n_suggesters, net_votes, first_suggested_min_ago, theme, target_energy,
          affinity, recent_artists, played_recently, block_explicit}
    Returns (score, components)."""
    c = {}
    c["demand"] = WEIGHTS["demand"] * math.log1p(ctx.get("n_suggesters", 0))
    c["momentum"] = WEIGHTS["momentum"] * math.tanh(ctx.get("net_votes", 0) / 3.0)

    th = ctx.get("theme")
    t_match = 0.0
    if th:
        kws = set(th.get("keywords") or [])
        genres = {g.lower() for g in track.get("genres", [])}
        hay = f"{track.get('title', '')} {track.get('artist', '')}".lower()
        hits = sum(1 for k in kws if any(k in g for g in genres) or k in hay)
        if kws:
            t_match += min(1.0, hits / max(1, min(len(kws), 2)))
        if th.get("year_from") and track.get("year"):
            t_match += 0.8 if th["year_from"] <= track["year"] <= th["year_to"] else -0.4
        if not kws and not th.get("year_from"):
            t_match = 0.0
    c["theme"] = WEIGHTS["theme"] * max(-0.4, min(1.5, t_match))

    e = track.get("energy")
    if e is not None and ctx.get("target_energy") is not None:
        c["energy"] = WEIGHTS["energy"] * (1.0 - min(1.0, abs(e - ctx["target_energy"]) / 0.35))
    else:
        c["energy"] = 0.0

    aff = ctx.get("affinity") or {}
    g_aff = [aff[g] for g in track.get("genres", []) if g in aff]
    c["affinity"] = WEIGHTS["affinity"] * (sum(g_aff) / len(g_aff) if g_aff else 0.0)

    age = ctx.get("first_suggested_min_ago", 0.0)
    c["freshness"] = WEIGHTS["freshness"] * math.exp(-age / 30.0) if ctx.get("n_suggesters", 0) else 0.0

    artist = (track.get("artist") or "").lower()
    recent = [a.lower() for a in ctx.get("recent_artists", [])]
    c["repeat"] = WEIGHTS["repeat"] if any(a and (a in artist or artist in a) for a in recent) else 0.0
    c["replay"] = WEIGHTS["replay"] if ctx.get("played_recently") else 0.0
    c["explicit"] = WEIGHTS["explicit"] if (ctx.get("block_explicit") and track.get("explicit")) else 0.0
    c["popularity"] = WEIGHTS["popularity"] * ((track.get("popularity") or 50) / 100.0)

    return round(sum(c.values()), 3), {k: round(v, 2) for k, v in c.items()}


def explain(components, ctx, track):
    """One human sentence for the big screen."""
    top = sorted(((v, k) for k, v in components.items() if v > 0.05), reverse=True)[:2]
    reasons = []
    for v, k in top:
        if k == "demand":
            n = ctx.get("n_suggesters", 0)
            reasons.append(f"{n} {'person' if n == 1 else 'people'} asked for it")
        elif k == "momentum":
            reasons.append(f"+{ctx.get('net_votes', 0)} on the queue")
        elif k == "theme" and ctx.get("theme"):
            reasons.append(f"fits “{ctx['theme'].get('text', 'the theme')}”")
        elif k == "energy":
            reasons.append("matches the room's energy")
        elif k == "affinity":
            reasons.append("the room's been loving this sound")
        elif k == "popularity":
            reasons.append("a safe crowd pick")
        elif k == "freshness":
            reasons.append("just requested")
    if components.get("repeat", 0) < 0:
        reasons.append("despite a recent song by this artist")
    return "; ".join(reasons) if reasons else "best available fit"


def should_skip(up, down, min_votes, net_ratio):
    n = up + down
    return n >= min_votes and (up - down) / n <= net_ratio


def minutes_since(iso, now=None):
    now = now or datetime.now(timezone.utc)
    dt = datetime.strptime(iso[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return (now - dt).total_seconds() / 60.0
