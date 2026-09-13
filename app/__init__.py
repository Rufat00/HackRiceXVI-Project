import json

from flask import Flask, redirect, request, send_from_directory

from .config import Config
from .db import get_db, init_db
from .services.ai import AI
from .services.spotify import SpotifyClient


def create_app(overrides=None):
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.config.from_object(Config)
    if overrides:
        app.config.update(overrides)
    init_db(app)

    app.extensions["spotify"] = None if app.config["SPOTIFY_MOCK"] else SpotifyClient(
        app.config["SPOTIFY_CLIENT_ID"], app.config["SPOTIFY_CLIENT_SECRET"], app.config["SPOTIFY_REDIRECT_URI"])
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
        return redirect(f"/host/{state}?spotify=connected")

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "spotify_mock": app.config["SPOTIFY_MOCK"], "ai": app.extensions["ai"].enabled}

    return app
