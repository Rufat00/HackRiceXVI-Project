from flask import Flask, send_from_directory

from .config import Config
from .db import init_db
from .services import nessie, persona


def create_app(overrides=None):
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.config.from_object(Config)
    if overrides:
        app.config.update(overrides)

    init_db(app)
    app.extensions["nessie"] = nessie.build_client(app.config)
    app.extensions["persona"] = persona.build_client(app.config)

    from .routes.api import api
    app.register_blueprint(api)

    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.get("/healthz")
    def healthz():
        return {"ok": True, "nessie_mock": app.config["NESSIE_MOCK"], "persona_mock": app.config["PERSONA_MOCK"]}

    return app
