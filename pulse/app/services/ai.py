"""Optional Claude layer. With ANTHROPIC_API_KEY set:
  * interpret_theme("songs my mom would dance to") -> genres, decade, energy
    (the regex parser in dj.py handles the obvious ones; Claude handles vibes)
  * shoutout(...) -> one-line DJ commentary for the big screen
Without a key both return None and the app behaves exactly the same."""
import json

import requests

from .dj import parse_theme

URL = "https://api.anthropic.com/v1/messages"


class AI:
    def __init__(self, api_key, model):
        self.key, self.model = api_key, model
        self.enabled = bool(api_key)

    def _ask(self, system, user, max_tokens=300):
        r = requests.post(URL, headers={"x-api-key": self.key, "anthropic-version": "2023-06-01",
                                        "content-type": "application/json"},
                          json={"model": self.model, "max_tokens": max_tokens, "system": system,
                                "messages": [{"role": "user", "content": user}]}, timeout=20)
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json().get("content", []))

    def interpret_theme(self, text):
        base = parse_theme(text)
        if not self.enabled:
            return base
        try:
            out = self._ask(
                "You turn a party theme into music filters. Reply with ONLY compact JSON: "
                '{"keywords":[lowercase genre or style tags, max 6],"year_from":int|null,"year_to":int|null,'
                '"target_energy":0..1|null}. Use common Spotify genre names (hip hop, dance pop, reggaeton, afrobeats, '
                "house, r&b, indie pop, pop punk, disco, country...). No prose.",
                f"Theme: {text}", 200)
            d = json.loads(out.strip().strip("`").removeprefix("json"))
            merged = dict(base)
            merged["keywords"] = sorted(set(base["keywords"]) | set(k.lower() for k in d.get("keywords", [])))[:8]
            merged["year_from"] = base["year_from"] or d.get("year_from")
            merged["year_to"] = base["year_to"] or d.get("year_to")
            merged["target_energy"] = base["target_energy"] if base["target_energy"] is not None else d.get("target_energy")
            return merged
        except Exception:
            return base

    def shoutout(self, track, explanation, theme_text, up, down):
        if not self.enabled:
            return None
        try:
            return self._ask(
                "You are the on-screen voice of an AI party DJ. One sentence, under 18 words, playful, no emojis, "
                "no hashtags, never mention that you are an AI.",
                f"Now playing: {track['title']} by {track['artist']}. Picked because: {explanation}. "
                f"Theme: {theme_text or 'none'}. Room reaction so far: {up} up, {down} down.", 60).strip()
        except Exception:
            return None
