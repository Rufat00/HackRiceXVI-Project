"""Configuration. Everything external is optional: with no keys set, the app
runs fully in mock mode so the demo works with zero network access."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_bool(name, default=False):
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-me")
    DATABASE_PATH = os.environ.get("DATABASE_PATH", str(BASE_DIR / "vouch.db"))

    # --- Capital One Nessie ------------------------------------------------
    # http://api.nessieisreal.com  (get a key at nessieisreal.com)
    NESSIE_API_KEY = os.environ.get("NESSIE_API_KEY", "")
    NESSIE_BASE_URL = os.environ.get("NESSIE_BASE_URL", "http://api.nessieisreal.com")
    NESSIE_MOCK = _env_bool("NESSIE_MOCK", default=not bool(NESSIE_API_KEY))

    # --- Persona ------------------------------------------------------------
    # Sandbox keys + a template id from the Persona dashboard.
    PERSONA_API_KEY = os.environ.get("PERSONA_API_KEY", "")
    PERSONA_TEMPLATE_ID = os.environ.get("PERSONA_TEMPLATE_ID", "")
    PERSONA_ENVIRONMENT_ID = os.environ.get("PERSONA_ENVIRONMENT_ID", "")
    PERSONA_ENVIRONMENT = os.environ.get("PERSONA_ENVIRONMENT", "sandbox")
    PERSONA_WEBHOOK_SECRET = os.environ.get("PERSONA_WEBHOOK_SECRET", "")
    PERSONA_BASE_URL = os.environ.get("PERSONA_BASE_URL", "https://withpersona.com/api/v1")
    PERSONA_MOCK = _env_bool(
        "PERSONA_MOCK", default=not (PERSONA_API_KEY and PERSONA_TEMPLATE_ID)
    )

    # --- Escrow policy --------------------------------------------------------
    # Seconds. Defaults are demo-short so timers visibly fire during judging.
    # Production would be days (e.g. 3 days to hand off, 24h to confirm).
    HANDOFF_DEADLINE_SECONDS = int(os.environ.get("HANDOFF_DEADLINE_SECONDS", 60 * 60 * 24 * 3))
    CONFIRM_WINDOW_SECONDS = int(os.environ.get("CONFIRM_WINDOW_SECONDS", 60 * 60 * 24))
    # Sublet deposits above this need a verified address document.
    SUBLET_UNVERIFIED_CAP_CENTS = int(os.environ.get("SUBLET_UNVERIFIED_CAP_CENTS", 50000))
    MIN_AGE = int(os.environ.get("MIN_AGE", 18))

    # Admins by email (comma separated). First registered user is also admin
    # when this is empty, so the demo always has one.
    ADMIN_EMAILS = [e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()]

    PLATFORM_NAME = "Vouch"
