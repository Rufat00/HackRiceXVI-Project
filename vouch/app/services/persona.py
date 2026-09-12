"""Persona identity verification.

Flow (real mode):
  1. Frontend opens the Persona embedded widget with our template id.
  2. Widget completes -> frontend POSTs the inquiry id to /api/verify/complete.
  3. We fetch the inquiry server-side (never trust the browser) and only mark
     the user verified if Persona says the inquiry passed.
  4. Persona also posts webhooks to /api/webhooks/persona; we verify the
     HMAC signature and apply the same logic. Either path can win.

Flow (mock mode, no keys): the frontend shows a sandbox-style pass/fail
toggle and POSTs the outcome. Same server-side function is called, so the
rest of the app cannot tell the difference.

One human, one account: Persona groups inquiries from the same person under
one Account object. We store that account id as the identity fingerprint and
the DB enforces uniqueness on it. If Persona hasn't linked an account we fall
back to a hash of legal name + birthdate.

Docs: https://docs.withpersona.com/reference/inquiries
"""
import hashlib
import hmac
import time
import uuid
from datetime import date, datetime

import requests

PASSED_STATUSES = {"completed", "approved"}
FAILED_STATUSES = {"failed", "declined", "expired", "marked-for-review"}


class PersonaError(Exception):
    pass


class VerificationResult:
    def __init__(self, passed, inquiry_id, reference_id, first_name, last_name, birthdate, status, raw=None):
        self.passed = passed
        self.inquiry_id = inquiry_id
        self.reference_id = reference_id
        self.first_name = first_name
        self.last_name = last_name
        self.birthdate = birthdate      # ISO string or None
        self.status = status
        self.raw = raw or {}

    def age(self, today=None):
        if not self.birthdate:
            return None
        try:
            b = datetime.strptime(self.birthdate[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
        today = today or date.today()
        return today.year - b.year - ((today.month, today.day) < (b.month, b.day))


def identity_fingerprint(account_id, first, last, birthdate):
    if account_id:
        return f"acct:{account_id}"
    basis = f"{(first or '').strip().lower()}|{(last or '').strip().lower()}|{(birthdate or '')[:10]}"
    return "hash:" + hashlib.sha256(basis.encode()).hexdigest()[:32]


class PersonaClient:
    def __init__(self, api_key, base_url):
        self.key = api_key
        self.base = base_url.rstrip("/")
        self.mock = False

    def fetch_inquiry(self, inquiry_id):
        try:
            r = requests.get(
                f"{self.base}/inquiries/{inquiry_id}",
                headers={
                    "Authorization": f"Bearer {self.key}",
                    "Persona-Version": "2023-01-05",
                    "Accept": "application/json",
                },
                timeout=15,
            )
        except requests.RequestException as e:
            raise PersonaError(f"Persona unreachable: {e}") from e
        if r.status_code >= 400:
            raise PersonaError(f"Persona inquiry fetch -> {r.status_code}: {r.text[:300]}")
        return self._parse(r.json().get("data", {}))

    @staticmethod
    def _parse(data):
        attrs = data.get("attributes", {}) or {}
        rel = data.get("relationships", {}) or {}
        account_id = ((rel.get("account") or {}).get("data") or {}).get("id")
        status = (attrs.get("status") or "").lower()
        first = attrs.get("name-first")
        last = attrs.get("name-last")
        birthdate = attrs.get("birthdate")
        return VerificationResult(
            passed=status in PASSED_STATUSES,
            inquiry_id=data.get("id"),
            reference_id=identity_fingerprint(account_id, first, last, birthdate),
            first_name=first, last_name=last, birthdate=birthdate,
            status=status, raw=data,
        )

    def parse_webhook(self, payload):
        """Webhook payload -> VerificationResult or None if not an inquiry event."""
        try:
            event = payload["data"]["attributes"]
            name = event.get("name", "")
            if not name.startswith("inquiry."):
                return None
            inquiry = event["payload"]["data"]
        except (KeyError, TypeError):
            return None
        return self._parse(inquiry)


def verify_webhook_signature(secret, header_value, raw_body, tolerance=300):
    """Persona-Signature: 't=<unix>,v1=<hmac hex>'. HMAC-SHA256 over 't.body'."""
    if not secret:
        return True  # explicitly unconfigured; caller decides
    if not header_value:
        return False
    parts = dict(p.split("=", 1) for p in header_value.split(",") if "=" in p)
    t, v1 = parts.get("t"), parts.get("v1")
    if not t or not v1:
        return False
    try:
        if abs(time.time() - int(t)) > tolerance:
            return False
    except ValueError:
        return False
    expected = hmac.new(secret.encode(), f"{t}.{raw_body.decode()}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, v1)


class MockPersonaClient:
    """Sandbox stand-in. The frontend sends {"outcome": "pass"|"fail", ...}."""

    def __init__(self):
        self.mock = True

    def from_mock_payload(self, payload):
        outcome = (payload.get("outcome") or "pass").lower()
        first = (payload.get("first_name") or "Sam").strip()
        last = (payload.get("last_name") or "Owl").strip()
        birthdate = payload.get("birthdate") or "2003-04-12"
        inquiry_id = payload.get("inquiry_id") or f"inq_mock_{uuid.uuid4().hex[:16]}"
        return VerificationResult(
            passed=(outcome == "pass"),
            inquiry_id=inquiry_id,
            # Mock has no Account object, so identity = name + DOB hash.
            reference_id=identity_fingerprint(None, first, last, birthdate),
            first_name=first, last_name=last, birthdate=birthdate,
            status="completed" if outcome == "pass" else "failed",
        )

    def fetch_inquiry(self, inquiry_id):
        raise PersonaError("mock mode has no inquiries to fetch")

    def parse_webhook(self, payload):
        return None


def build_client(config):
    if config["PERSONA_MOCK"]:
        return MockPersonaClient()
    return PersonaClient(config["PERSONA_API_KEY"], config["PERSONA_BASE_URL"])
