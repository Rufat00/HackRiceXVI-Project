"""Small YouTube Data API client used for public music search.

Playback is deliberately handled by the official IFrame Player API in the
host browser. The server only searches videos and normalizes their metadata
into Pulse's existing track shape.
"""
import re
from html import unescape
from datetime import datetime

import requests


API = "https://www.googleapis.com/youtube/v3"
NON_SONG_TITLE = re.compile(
    r"\b(full album|album stream|playlist|compilation|mega\s*mix|dj set|music mix|"
    r"one hour|1 hour|loop(?:ed)?|reaction|tutorial|karaoke|instrumental collection)\b",
    re.IGNORECASE,
)


class YouTubeError(Exception):
    pass


def parse_duration(value):
    """Convert the subset of ISO-8601 durations returned by YouTube to ms."""
    m = re.fullmatch(r"P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", value or "")
    if not m:
        return 0
    days, hours, minutes, seconds = (int(n or 0) for n in m.groups())
    return (((days * 24 + hours) * 60 + minutes) * 60 + seconds) * 1000


def _title_artist(title, channel):
    title = unescape(title or "")
    channel = unescape(channel or "YouTube")
    channel = re.sub(r"\s*-\s*Topic$", "", channel or "YouTube").strip()
    # Official music uploads commonly use "Artist - Song". This makes the
    # guest results much nicer without pretending YouTube has Spotify-style
    # artist metadata.
    parts = re.split(r"\s+[-–—]\s+", title, maxsplit=1)
    if len(parts) == 2 and len(parts[0]) <= 80:
        return parts[1].strip(), parts[0].strip()
    return (title or "Untitled").strip(), channel


def _audio_profile(text):
    """Infer enough broad metadata for the vibe scorer and beat animation."""
    value = (text or "").lower()
    genre_terms = {
        "latin": ("latin", "reggaeton", "salsa", "bachata", "merengue"),
        "hip hop": ("hip hop", "hip-hop", "rap", "trap"),
        "r&b": ("r&b", "rnb", "soul"),
        "electronic": ("edm", "house", "techno", "electronic", "dance"),
        "rock": ("rock", "punk", "metal", "indie"),
        "country": ("country",),
        "reggae": ("reggae", "dancehall"),
        "afrobeats": ("afrobeats", "afrobeat", "amapiano"),
        "jazz": ("jazz",),
        "pop": ("pop",),
    }
    genres = [genre for genre, terms in genre_terms.items() if any(term in value for term in terms)]
    low = ("chill", "slow", "acoustic", "ballad", "sleep", "lofi", "lo-fi")
    high = ("upbeat", "dance", "party", "banger", "edm", "house", "techno", "club", "hype")
    energy = .38 if any(term in value for term in low) else .84 if any(term in value for term in high) else .62
    tempo = 92 if energy < .5 else 128 if energy > .75 else 112
    return genres or ["music"], energy, tempo


class YouTubeClient:
    def __init__(self, api_key):
        self.api_key = api_key

    def _get(self, path, **params):
        try:
            r = requests.get(
                API + path,
                params=params,
                headers={"X-Goog-Api-Key": self.api_key},
                timeout=15,
            )
        except requests.RequestException as e:
            raise YouTubeError(f"YouTube request failed: {e}") from e
        if r.status_code >= 400:
            try:
                detail = r.json().get("error", {}).get("message")
            except ValueError:
                detail = None
            raise YouTubeError(detail or f"YouTube returned HTTP {r.status_code}")
        return r.json()

    def search(self, query, limit=10):
        query = (query or "party music").strip()[:120]
        search_query = f"{query} official audio"
        found = self._get(
            "/search",
            part="snippet",
            q=search_query,
            type="video",
            videoCategoryId="10",
            videoEmbeddable="true",
            videoSyndicated="true",
            safeSearch="moderate",
            maxResults=max(12, min(int(limit) + 5, 25)),
        ).get("items", [])
        ids = [item.get("id", {}).get("videoId") for item in found]
        ids = [video_id for video_id in ids if video_id]
        if not ids:
            return []

        details = self._get(
            "/videos",
            part="contentDetails,status,statistics,topicDetails",
            id=",".join(ids),
        ).get("items", [])
        by_id = {item.get("id"): item for item in details}
        merged = []
        for result in found:
            video_id = result.get("id", {}).get("videoId")
            item = dict(by_id.get(video_id, {}))
            item["id"] = video_id
            item["snippet"] = result.get("snippet") or {}
            merged.append(item)
        return self._tracks(merged, query, limit)

    def trending(self, region="US", limit=20):
        """Return YouTube's mainstream music chart without a text search."""
        items = self._get(
            "/videos",
            part="snippet,contentDetails,status,statistics,topicDetails",
            chart="mostPopular",
            regionCode=(region or "US").upper(),
            videoCategoryId="10",
            maxResults=max(20, min(int(limit) + 10, 50)),
        ).get("items", [])
        return self._tracks(items, "mainstream chart music", limit)

    def _tracks(self, items, context, limit):
        tracks = []
        for item in items:
            video_id = item.get("id")
            if not video_id or item.get("status", {}).get("embeddable") is False:
                continue
            snippet = item.get("snippet") or {}
            if snippet.get("liveBroadcastContent", "none") != "none":
                continue
            title, artist = _title_artist(snippet.get("title"), snippet.get("channelTitle"))
            thumbs = snippet.get("thumbnails") or {}
            thumb = thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}
            published = snippet.get("publishedAt") or ""
            try:
                year = datetime.fromisoformat(published.replace("Z", "+00:00")).year
            except ValueError:
                year = None
            duration = parse_duration(item.get("contentDetails", {}).get("duration"))
            # Keep the queue to individual songs: reject shorts, long mixes,
            # albums and compilations. The IFrame FINISH event remains the
            # final authority for actual playback completion.
            if not duration or duration < 45_000 or duration > 12 * 60_000:
                continue
            if NON_SONG_TITLE.search(title):
                continue
            tags = " ".join(snippet.get("tags") or [])
            topics = " ".join(item.get("topicDetails", {}).get("topicCategories") or [])
            genres, energy, tempo = _audio_profile(f"{context} {title} {artist} {tags} {topics}")
            views = int(item.get("statistics", {}).get("viewCount") or 0)
            popularity = min(100, max(0, int((len(str(views)) - 1) * 12.5))) if views else 50
            tracks.append({
                "id": f"youtube:{video_id}",
                "title": title,
                "artist": artist,
                "album": "YouTube",
                "year": year,
                "duration_ms": duration,
                "genres": genres,
                "energy": energy,
                "danceability": None,
                "valence": None,
                "tempo": tempo,
                "popularity": popularity,
                "art_url": thumb.get("url"),
                "preview_url": None,
                "explicit": 0,
            })
        # Prefer official/topic/audio uploads while retaining YouTube's
        # relevance ordering within each group.
        tracks.sort(key=lambda t: 0 if re.search(r"official|audio|topic", f"{t['title']} {t['artist']}", re.I) else 1)
        return tracks[:limit]
