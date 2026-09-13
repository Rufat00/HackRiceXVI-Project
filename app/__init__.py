import json

from flask import Flask, redirect, request, send_from_directory

from .config import Config
from .db import get_db, init_db
from .services.ai import AI
from .services.spotify import SpotifyClient
from .services.youtube import YouTubeClient


def create_app(overrides=None):
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.config.from_object(Config)
    if overrides:
        app.config.update(overrides)
    init_db(app)

    app.extensions["spotify"] = None if app.config["SPOTIFY_MOCK"] else SpotifyClient(
        app.config["SPOTIFY_CLIENT_ID"], app.config["SPOTIFY_CLIENT_SECRET"], app.config["SPOTIFY_REDIRECT_URI"])
    app.extensions["youtube"] = YouTubeClient(app.config["YOUTUBE_API_KEY"]) if app.config["YOUTUBE_API_KEY"] else None
    app.extensions["ai"] = AI(app.config["ANTHROPIC_API_KEY"], app.config["ANTHROPIC_MODEL"])

    from .routes.api import api
    app.register_blueprint(api)

    @app.get("/")
    def landing():
        return send_from_directory(app.static_folder, "landing.html")

    @app.get("/host/<code>")
    def host_page(code):
        return send_from_directory(app.static_folder, "host.html")

    @app.get("/join/<code>")
    def guest_page(code):
        return send_from_directory(app.static_folder, "guest.html")

    @app.get("/callback")
    def spotify_callback():
        """Spotify redirects here after the host authorizes. state = party code."""
        sp = app.extensions["spotify"]
        code, state = request.args.get("code"), request.args.get("state", "")
        if not sp or not code:
            return redirect(f"/host/{state}?spotify=error")
        tokens = sp.exchange_code(code)
        db = get_db()
        db.execute("UPDATE parties SET spotify_tokens=? WHERE code=?", (json.dumps(tokens), state))
        db.commit()
        # Auto-seed a small set of real, verified-URI songs the first time a
        # party connects to Spotify, so there's something real to play
        # immediately without needing /search (the endpoint most likely to
        # be rate-limited) or a manual "Load test tracks" click.
        try:
            party = db.execute("SELECT * FROM parties WHERE code=?", (state,)).fetchone()
            host_guest = db.execute("SELECT id FROM guests WHERE party_id=? AND is_host=1", (party["id"],)).fetchone()
            if party and host_guest:
                from .services import party as P
                from .services.real_test_tracks import seed_and_suggest
                seed_and_suggest(party, host_guest["id"], P.upsert_track, db)
        except Exception as e:
            # Never let a seeding hiccup break the OAuth flow itself.
            app.logger.warning("auto-seed after Spotify connect failed: %s", e)
        return redirect(f"/host/{state}?spotify=connected")

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "spotify_mock": app.config["SPOTIFY_MOCK"],
                "youtube": app.extensions["youtube"] is not None, "ai": app.extensions["ai"].enabled}

    return app
