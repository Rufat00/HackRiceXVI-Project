"""
Persona identity verification.

Flow:
  1. Frontend opens the Persona hosted widget (needs PERSONA_TEMPLATE_ID and
     PERSONA_ENV_ID, both public, from your sandbox dashboard).
  2. On completion the widget returns an inquiry_id + status to the browser.
  3. Browser posts the inquiry_id to /api/verify; we confirm server-side with
     the Persona API so a client can't just claim it passed.

In mock mode (no PERSONA_API_KEY) the frontend shows a pass/fail switch that
mirrors the sandbox toggle, and /api/verify trusts the posted status.
"""
import os
import requests

API_KEY = os.environ.get("PERSONA_API_KEY", "")
TEMPLATE_ID = os.environ.get("PERSONA_TEMPLATE_ID", "")
ENV_ID = os.environ.get("PERSONA_ENV_ID", "")
MOCK = not API_KEY

BASE = "https://api.withpersona.com/api/v1"
HEADERS = {"Authorization": f"Bearer {API_KEY}",
           "Persona-Version": "2023-01-05", "Accept": "application/json"}


class PersonaError(Exception):
    pass


def check_inquiry(inquiry_id, claimed_status=None):
    """
    Returns dict: {"verified": bool, "status": str, "name": str|None}
    """
    if MOCK:
        status = (claimed_status or "failed").lower()
        return {"verified": status in ("completed", "approved"),
                "status": status, "name": None}

    r = requests.get(f"{BASE}/inquiries/{inquiry_id}", headers=HEADERS,
                     timeout=15)
    if r.status_code >= 400:
        raise PersonaError(f"Persona {r.status_code}: {r.text[:200]}")
    attrs = r.json()["data"]["attributes"]
    status = attrs.get("status", "")
    first = attrs.get("name-first") or ""
    last = attrs.get("name-last") or ""
    name = f"{first} {last}".strip() or None
    return {"verified": status in ("completed", "approved"),
            "status": status, "name": name}


def public_config():
    return {"mock": MOCK, "template_id": TEMPLATE_ID, "environment_id": ENV_ID}
