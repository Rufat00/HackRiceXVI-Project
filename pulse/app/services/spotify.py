"""Spotify integration. The HOST authorizes once; the app then searches the
catalog and drives playback on whatever device the host has Spotify open on
(Spotify Connect). Guests never touch Spotify.

Notes for whoever wires this up live:
  * Redirect URI must be registered exactly (Spotify no longer accepts
    'localhost'; use http://127.0.0.1:5000/callback).
  * Playback control requires the host to have Spotify Premium and an
    active device (open the Spotify app and press play once).
  * /audio-features was restricted for new apps in late 2024. We try it and
    silently fall back to None; the DJ scorer treats unknown energy as neutral
    and leans on votes/theme/genre instead.
"""
import base64
import json
import time
import urllib.parse

import requests

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API = "https://api.spotify.com/v1"
SCOPES = "user-read-playback-state user-modify-playback-state user-read-currently-playing"


class SpotifyError(Exception):
    pass


class SpotifyClient:
    def __init__(self, client_id, client_secret, redirect_uri):
        self.cid, self.secret, self.redirect = client_id, client_secret, redirect_uri
        self.mock = False
        self._genre_cache = {}

    # ---- OAuth ------------------------------------------------------------
    def auth_url(self, state):
        return AUTH_URL + "?" + urllib.parse.urlencode({
            "client_id": self.cid, "response_type": "code", "redirect_uri": self.redirect,
            "scope": SCOPES, "state": state, "show_dialog": "true",
        })

    def _basic(self):
        return {"Authorization": "Basic " + base64.b64encode(f"{self.cid}:{self.secret}".encode()).decode()}

    def exchange_code(self, code):
        r = requests.post(TOKEN_URL, data={"grant_type": "authorization_code", "code": code,
                                           "redirect_uri": self.redirect}, headers=self._basic(), timeout=15)
        if r.status_code >= 400:
            raise SpotifyError(f"token exchange failed: {r.text[:200]}")
        d = r.json()
        return {"access": d["access_token"], "refresh": d.get("refresh_token"),
                "expires_at": time.time() + d.get("expires_in", 3600) - 60}

    def refresh(self, tokens):
        r = requests.post(TOKEN_URL, data={"grant_type": "refresh_token", "refresh_token": tokens["refresh"]},
                          headers=self._basic(), timeout=15)
        if r.status_code >= 400:
            raise SpotifyError(f"token refresh failed: {r.text[:200]}")
        d = r.json()
        return {"access": d["access_token"], "refresh": d.get("refresh_token", tokens["refresh"]),
                "expires_at": time.time() + d.get("expires_in", 3600) - 60}

    def ensure_fresh(self, tokens):
        """Returns (tokens, changed)."""
        if tokens and tokens.get("expires_at", 0) < time.time():
            return self.refresh(tokens), True
        return tokens, False

    # ---- HTTP ---------------------------------------------------------------
    def _req(self, tokens, method, path, **kw):
        r = requests.request(method, API + path, headers={"Authorization": f"Bearer {tokens['access']}"},
                             timeout=15, **kw)
        if r.status_code == 204:
            return {}
        if r.status_code >= 400:
            raise SpotifyError(f"{method} {path} -> {r.status_code}: {r.text[:200]}")
        return r.json() if r.text else {}

    # ---- Catalog --------------------------------------------------------------
    def search(self, tokens, q, limit=12):
        d = self._req(tokens, "GET", "/search", params={"q": q, "type": "track", "limit": limit})
        items = d.get("tracks", {}).get("items", [])
        tracks = [self._track(t) for t in items]
        self._enrich_genres(tokens, tracks, items)
        self._enrich_features(tokens, tracks)
        return tracks

    @staticmethod
    def _track(t):
        imgs = (t.get("album") or {}).get("images") or []
        date = (t.get("album") or {}).get("release_date") or ""
        return {
            "id": t["uri"], "title": t["name"], "artist": ", ".join(a["name"] for a in t.get("artists", [])),
            "album": (t.get("album") or {}).get("name"), "year": int(date[:4]) if date[:4].isdigit() else None,
            "duration_ms": t["duration_ms"], "genres": [], "energy": None, "danceability": None,
            "valence": None, "tempo": None, "popularity": t.get("popularity"),
            "art_url": imgs[1]["url"] if len(imgs) > 1 else (imgs[0]["url"] if imgs else None),
            "preview_url": t.get("preview_url"), "explicit": int(bool(t.get("explicit"))),
            "_artist_ids": [a["id"] for a in t.get("artists", [])],
        }

    def _enrich_genres(self, tokens, tracks, _items):
        ids = {aid for t in tracks for aid in t["_artist_ids"] if aid not in self._genre_cache}
        ids = list(ids)[:50]
        if ids:
            try:
                d = self._req(tokens, "GET", "/artists", params={"ids": ",".join(ids)})
                for a in d.get("artists", []):
                    if a:
                        self._genre_cache[a["id"]] = a.get("genres", [])
            except SpotifyError:
                pass
        for t in tracks:
            g = []
            for aid in t.pop("_artist_ids"):
                g += self._genre_cache.get(aid, [])
            t["genres"] = sorted(set(g))[:8]

    def _enrich_features(self, tokens, tracks):
        ids = [t["id"].split(":")[-1] for t in tracks]
        if not ids:
            return
        try:
            d = self._req(tokens, "GET", "/audio-features", params={"ids": ",".join(ids)})
        except SpotifyError:
            return  # restricted for newer apps; scorer copes with None
        for t, f in zip(tracks, d.get("audio_features", [])):
            if f:
                t.update(energy=f.get("energy"), danceability=f.get("danceability"),
                         valence=f.get("valence"), tempo=f.get("tempo"))

    # ---- Playback -----------------------------------------------------------------
    def state(self, tokens):
        """None if nothing is active. Else {track_id, progress_ms, duration_ms, is_playing, device}."""
        d = self._req(tokens, "GET", "/me/player")
        if not d or not d.get("item"):
            return None
        return {"track_id": d["item"]["uri"], "progress_ms": d.get("progress_ms", 0),
                "duration_ms": d["item"]["duration_ms"], "is_playing": d.get("is_playing", False),
                "device": (d.get("device") or {}).get("name")}

    def play(self, tokens, track_uri):
        self._req(tokens, "PUT", "/me/player/play", json={"uris": [track_uri]})

    def pause(self, tokens):
        self._req(tokens, "PUT", "/me/player/pause")

    def resume(self, tokens):
        self._req(tokens, "PUT", "/me/player/play")

    def devices(self, tokens):
        return self._req(tokens, "GET", "/me/player/devices").get("devices", [])

    def get_track(self, tokens, track_uri):
        t = self._req(tokens, "GET", f"/tracks/{track_uri.split(':')[-1]}")
        tr = self._track(t)
        self._enrich_genres(tokens, [tr], [t])
        self._enrich_features(tokens, [tr])
        return tr
