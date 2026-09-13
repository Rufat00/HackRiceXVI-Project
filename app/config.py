import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_local_env(path):
    """Load the project's simple KEY=value file without another dependency."""
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if not key or not key.replace("_", "").isalnum():
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ.setdefault(key, value)


_load_local_env(BASE_DIR / ".env")


def _b(name, default):
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-me")
    DATABASE_PATH = os.environ.get("DATABASE_PATH", str(BASE_DIR / "pulse.db"))
    PUBLIC_URL = os.environ.get("PUBLIC_URL", "")  # e.g. https://abc.ngrok.app ; used in the QR code

    # Spotify (host account controls playback). Redirect URI must be registered
    # in the Spotify dashboard EXACTLY as http://127.0.0.1:5000/callback.
    SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID", "")
    SPOTIFY_CLIENT_SECRET = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
    SPOTIFY_REDIRECT_URI = os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:5000/callback")
    SPOTIFY_MOCK = _b("SPOTIFY_MOCK", not (SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET))

    # YouTube is the zero-install browser playback path. The Data API is only
    # used for public search; the host page uses the IFrame API for playback.
    YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")
    YOUTUBE_REGION_CODE = os.environ.get("YOUTUBE_REGION_CODE", "US")

    # Optional: Claude interprets themes and writes DJ shoutouts.
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

    # Mock player speed: 1.0 = real time. 30 = a 3-minute song lasts 6 seconds (demo).
    MOCK_TIME_SCALE = float(os.environ.get("MOCK_TIME_SCALE", "1.0"))

    # DJ policy
    SKIP_MIN_VOTES = int(os.environ.get("SKIP_MIN_VOTES", 4))
    SKIP_NET_RATIO = float(os.environ.get("SKIP_NET_RATIO", -0.5))   # (up-down)/total <= this -> skip
    NO_REPLAY_MINUTES = int(os.environ.get("NO_REPLAY_MINUTES", 120))
    SUGGESTIONS_PER_GUEST_PER_HOUR = int(os.environ.get("SUGGESTIONS_PER_GUEST_PER_HOUR", 8))
