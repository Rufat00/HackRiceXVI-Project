import json
import random
import sqlite3
from datetime import datetime

from flask import Blueprint, current_app, g, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from ..auth import (admin_required, current_user, is_admin_email, login_required,
                    public_user, verified_required)
from ..db import get_db
from ..services import escrow, safety
from ..services.escrow import EscrowError
from ..services.nessie import NessieError
from ..services.persona import PersonaError, verify_webhook_signature

api = Blueprint("api", __name__, url_prefix="/api")

DEMO_STARTING_BALANCE_CENTS = 50000  # every new wallet starts with $500 of Nessie play money

PROOF_WORDS = [
    "pelican", "lantern", "cobalt", "saffron", "meadow", "quartz", "harbor", "velvet",
    "ember", "juniper", "marble", "orchid", "pebble", "raven", "sequoia", "tundra",
    "walnut", "zephyr", "anchor", "biscuit", "canyon", "dahlia", "falcon", "glacier",
]


# --------------------------------------------------------------------------
# Plumbing
# --------------------------------------------------------------------------
@api.before_request
def _lazy_timers():
    # Every request advances escrow timers. Cheap, and it means the demo shows
    # auto-refund / auto-release firing without a worker process.
    try:
        escrow.sweep()
    except Exception as e:  # never let a timer bug take down a request
        current_app.logger.warning("sweep failed: %s", e)


@api.errorhandler(EscrowError)
def _escrow_err(e):
    return jsonify(error=str(e)), e.status


@api.errorhandler(NessieError)
def _nessie_err(e):
    return jsonify(error=f"Bank error: {e}"), 502


@api.errorhandler(PersonaError)
def _persona_err(e):
    return jsonify(error=f"Identity provider error: {e}"), 502


def _body():
    return request.get_json(silent=True) or {}


def _row(r):
    return dict(r) if r is not None else None


# --------------------------------------------------------------------------
# Meta
# --------------------------------------------------------------------------
@api.get("/config")
def config():
    c = current_app.config
    return jsonify(
        platform=c["PLATFORM_NAME"],
        nessie_mock=c["NESSIE_MOCK"],
        persona_mock=c["PERSONA_MOCK"],
        persona_template_id=c["PERSONA_TEMPLATE_ID"],
        persona_environment_id=c["PERSONA_ENVIRONMENT_ID"],
        persona_environment=c["PERSONA_ENVIRONMENT"],
        handoff_deadline_seconds=c["HANDOFF_DEADLINE_SECONDS"],
        confirm_window_seconds=c["CONFIRM_WINDOW_SECONDS"],
        sublet_unverified_cap_cents=c["SUBLET_UNVERIFIED_CAP_CENTS"],
        min_age=c["MIN_AGE"],
    )


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------
@api.post("/auth/register")
def register():
    b = _body()
    email = (b.get("email") or "").strip().lower()
    pw = b.get("password") or ""
    name = (b.get("display_name") or "").strip()
    if "@" not in email or len(pw) < 6 or not name:
        return jsonify(error="Need an email, a display name, and a password of 6+ characters"), 400
    db = get_db()
    first_user = db.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 0
    is_admin = is_admin_email(email) or (first_user and not current_app.config["ADMIN_EMAILS"])
    try:
        cur = db.execute(
            "INSERT INTO users (email, password_hash, display_name, is_admin) VALUES (?,?,?,?)",
            (email, generate_password_hash(pw), name[:40], int(is_admin)),
        )
    except sqlite3.IntegrityError:
        return jsonify(error="That email already has an account"), 409
    uid = cur.lastrowid

    # Wallet: a Nessie customer + checking account seeded with play money.
    nessie = current_app.extensions["nessie"]
    parts = name.split()
    cust = nessie.create_customer(parts[0], parts[-1] if len(parts) > 1 else "Student")
    acct = nessie.create_account(cust, f"{name} checking", balance_cents=DEMO_STARTING_BALANCE_CENTS)
    db.execute("UPDATE users SET nessie_customer_id=?, nessie_account_id=? WHERE id=?", (cust, acct, uid))
    db.commit()
    session["uid"] = uid
    g.pop("user", None)
    return jsonify(user=me_payload()), 201


@api.post("/auth/login")
def login():
    b = _body()
    u = get_db().execute("SELECT * FROM users WHERE email=?", ((b.get("email") or "").strip().lower(),)).fetchone()
    if not u or not check_password_hash(u["password_hash"], b.get("password") or ""):
        return jsonify(error="Email or password didn't match"), 401
    session["uid"] = u["id"]
    g.pop("user", None)
    return jsonify(user=me_payload())


@api.post("/auth/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


def me_payload():
    u = current_user()
    if not u:
        return None
    d = public_user(u, include_private=True)
    d["reputation"] = escrow.reputation(u["id"])
    if u["nessie_account_id"]:
        try:
            d["wallet"] = current_app.extensions["nessie"].get_account(u["nessie_account_id"])
        except NessieError as e:
            d["wallet"] = {"error": str(e)}
    return d


@api.get("/me")
def me():
    return jsonify(user=me_payload())


@api.get("/users/<int:uid>")
def user_profile(uid):
    u = get_db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not u:
        return jsonify(error="No such user"), 404
    d = public_user(u)
    d["reputation"] = escrow.reputation(uid)
    d["recent_ratings"] = [_row(r) for r in get_db().execute(
        """SELECT r.stars, r.comment, r.created_at, u.display_name AS rater
           FROM ratings r JOIN users u ON u.id=r.rater_id WHERE r.ratee_id=?
           ORDER BY r.created_at DESC LIMIT 10""", (uid,)).fetchall()]
    return jsonify(user=d)


# --------------------------------------------------------------------------
# Identity verification (Persona)
# --------------------------------------------------------------------------
def _apply_verification(user_id, result):
    """Shared by the widget-complete path, the mock path, and the webhook."""
    db = get_db()
    if not result.passed:
        return False, f"Verification did not pass (status: {result.status})"
    age = result.age()
    min_age = current_app.config["MIN_AGE"]
    if age is not None and age < min_age:
        return False, f"You must be {min_age}+ to use {current_app.config['PLATFORM_NAME']}"

    # One human, one account.
    clash = db.execute(
        "SELECT id, display_name FROM users WHERE persona_reference_id=? AND id<>?",
        (result.reference_id, user_id),
    ).fetchone()
    if clash:
        return False, "This identity is already verified on another account. One person, one account."

    try:
        db.execute(
            """UPDATE users SET verified=1, verified_at=datetime('now'), persona_inquiry_id=?,
               persona_reference_id=?, legal_first_name=?, legal_last_name=?, birthdate=? WHERE id=?""",
            (result.inquiry_id, result.reference_id, result.first_name, result.last_name,
             (result.birthdate or "")[:10] or None, user_id),
        )
        db.commit()
    except sqlite3.IntegrityError:
        db.rollback()
        return False, "This identity is already verified on another account. One person, one account."
    g.pop("user", None)
    return True, "Verified"


@api.post("/verify/complete")
@login_required
def verify_complete():
    """Real mode: {inquiry_id}. Mock mode: {outcome, first_name, last_name, birthdate}."""
    u = current_user()
    if u["verified"]:
        return jsonify(ok=True, user=me_payload(), message="Already verified")
    persona = current_app.extensions["persona"]
    b = _body()
    if persona.mock:
        result = persona.from_mock_payload(b)
    else:
        inquiry_id = b.get("inquiry_id")
        if not inquiry_id:
            return jsonify(error="inquiry_id required"), 400
        result = persona.fetch_inquiry(inquiry_id)
    ok, msg = _apply_verification(u["id"], result)
    if not ok:
        return jsonify(error=msg, status=result.status), 403
    return jsonify(ok=True, user=me_payload(), message=msg)


@api.post("/webhooks/persona")
def persona_webhook():
    raw = request.get_data()
    secret = current_app.config["PERSONA_WEBHOOK_SECRET"]
    if not verify_webhook_signature(secret, request.headers.get("Persona-Signature"), raw):
        return jsonify(error="bad signature"), 401
    persona = current_app.extensions["persona"]
    result = persona.parse_webhook(request.get_json(silent=True) or {})
    if not result or not result.inquiry_id:
        return jsonify(ok=True, ignored=True)
    # Match the inquiry to a user via reference-id we set to the user id when opening the widget.
    ref = ((result.raw.get("attributes") or {}).get("reference-id") or "")
    if not ref.startswith("user:"):
        return jsonify(ok=True, ignored=True)
    uid = int(ref.split(":", 1)[1])
    ok, msg = _apply_verification(uid, result)
    return jsonify(ok=ok, message=msg)


# --------------------------------------------------------------------------
# Wallet (Nessie)
# --------------------------------------------------------------------------
@api.get("/wallet")
@login_required
def wallet():
    u = current_user()
    nessie = current_app.extensions["nessie"]
    acct = nessie.get_account(u["nessie_account_id"])
    return jsonify(account=acct, transfers=nessie.list_transfers(u["nessie_account_id"]), mock=nessie.mock)


@api.post("/wallet/topup")
@login_required
def wallet_topup():
    """Demo convenience: deposit play money so judges can watch balances move."""
    u = current_user()
    amt = int(_body().get("amount_cents") or 10000)
    if not 100 <= amt <= 500000:
        return jsonify(error="Top up between $1 and $5,000"), 400
    current_app.extensions["nessie"].deposit(u["nessie_account_id"], amt, "Vouch demo top-up")
    return jsonify(user=me_payload())


# --------------------------------------------------------------------------
# Listings
# --------------------------------------------------------------------------
def _listing_out(row, include_photo=True):
    d = _row(row)
    if d is None:
        return None
    d["price"] = d["price_cents"] / 100
    d.pop("proof_word", None) if d.get("status") != "draft" else None
    if not include_photo:
        d["has_photo"] = bool(d.pop("photo_data", None))
    return d


@api.get("/listings")
def listings():
    db = get_db()
    q = (request.args.get("q") or "").strip()
    cat = request.args.get("category")
    sql = """SELECT l.*, u.display_name AS seller_name, u.verified AS seller_verified
             FROM listings l JOIN users u ON u.id=l.seller_id WHERE l.status='active'"""
    args = []
    if q:
        sql += " AND (l.title LIKE ? OR l.description LIKE ?)"
        args += [f"%{q}%", f"%{q}%"]
    if cat in ("item", "ticket", "sublet"):
        sql += " AND l.category=?"
        args.append(cat)
    sql += " ORDER BY l.created_at DESC LIMIT 100"
    rows = db.execute(sql, args).fetchall()
    out = []
    for r in rows:
        d = _listing_out(r)
        d["seller_reputation"] = escrow.reputation(r["seller_id"])
        out.append(d)
    return jsonify(listings=out)


@api.get("/listings/mine")
@login_required
def my_listings():
    rows = get_db().execute(
        "SELECT * FROM listings WHERE seller_id=? ORDER BY created_at DESC", (current_user()["id"],)
    ).fetchall()
    return jsonify(listings=[_listing_out(r) for r in rows])


@api.get("/listings/<int:lid>")
def listing(lid):
    r = get_db().execute(
        """SELECT l.*, u.display_name AS seller_name, u.verified AS seller_verified
           FROM listings l JOIN users u ON u.id=l.seller_id WHERE l.id=?""", (lid,)
    ).fetchone()
    if not r:
        return jsonify(error="Listing not found"), 404
    d = _listing_out(r)
    d["seller_reputation"] = escrow.reputation(r["seller_id"])
    u = current_user()
    if u and u["id"] == r["seller_id"]:
        d["proof_word"] = r["proof_word"]
    return jsonify(listing=d)


@api.post("/listings")
@verified_required
def create_listing():
    """Step 1 of 2: creates a draft and returns the proof word. The listing
    only goes live once a photo showing that word is captured in-app."""
    b = _body()
    u = current_user()
    title = (b.get("title") or "").strip()
    cat = b.get("category")
    try:
        price_cents = int(round(float(b.get("price") or 0) * 100))
    except (TypeError, ValueError):
        price_cents = 0
    if not title or cat not in ("item", "ticket", "sublet") or price_cents <= 0:
        return jsonify(error="Need a title, a category, and a price above $0"), 400
    address = (b.get("address") or "").strip() or None
    if cat == "sublet":
        if not address:
            return jsonify(error="Sublets need an address"), 400
        cap = current_app.config["SUBLET_UNVERIFIED_CAP_CENTS"]
        if price_cents > cap:
            return jsonify(error=f"Sublet deposits above ${cap/100:,.0f} need a verified lease or "
                                 f"utility bill for the address. Lower the amount or bring the "
                                 f"document to a Vouch moderator."), 400
    word = random.choice(PROOF_WORDS)
    cur = get_db().execute(
        """INSERT INTO listings (seller_id, title, description, category, price_cents, proof_word, address)
           VALUES (?,?,?,?,?,?,?)""",
        (u["id"], title[:80], (b.get("description") or "").strip()[:1000], cat, price_cents, word, address),
    )
    get_db().commit()
    return jsonify(listing_id=cur.lastrowid, proof_word=word,
                   instructions=f"Write “{word}” on paper, put it next to the item, and take the photo in-app."), 201


@api.post("/listings/<int:lid>/photo")
@verified_required
def listing_photo(lid):
    """Step 2 of 2: in-app camera capture. We only accept a data URL that the
    browser produced from a live camera frame; there is no file upload path,
    which is what blocks stolen photos."""
    db = get_db()
    u = current_user()
    l = db.execute("SELECT * FROM listings WHERE id=?", (lid,)).fetchone()
    if not l or l["seller_id"] != u["id"]:
        return jsonify(error="Listing not found"), 404
    if l["status"] != "draft":
        return jsonify(error="This listing already has its photo"), 400
    data = _body().get("photo_data") or ""
    if not data.startswith("data:image/") or len(data) < 2000:
        return jsonify(error="Take the photo with the in-app camera"), 400
    if len(data) > 3_000_000:
        return jsonify(error="Photo too large"), 413
    db.execute(
        "UPDATE listings SET photo_data=?, photo_captured_at=datetime('now'), status='active' WHERE id=?",
        (data, lid),
    )
    db.commit()
    return jsonify(ok=True, listing=_listing_out(db.execute("SELECT * FROM listings WHERE id=?", (lid,)).fetchone()))


@api.delete("/listings/<int:lid>")
@login_required
def remove_listing(lid):
    db = get_db()
    l = db.execute("SELECT * FROM listings WHERE id=?", (lid,)).fetchone()
    if not l or l["seller_id"] != current_user()["id"]:
        return jsonify(error="Listing not found"), 404
    if l["status"] not in ("draft", "active"):
        return jsonify(error="Can't remove a listing with an open transaction"), 400
    db.execute("UPDATE listings SET status='removed' WHERE id=?", (lid,))
    db.commit()
    return jsonify(ok=True)


# --------------------------------------------------------------------------
# Transactions / escrow
# --------------------------------------------------------------------------
def _txn_out(row, viewer_id):
    db = get_db()
    d = _row(row)
    d.pop("handoff_code_hash", None)
    d["amount"] = d["amount_cents"] / 100
    d["role"] = "buyer" if viewer_id == d["buyer_id"] else "seller" if viewer_id == d["seller_id"] else "observer"
    d["listing"] = _listing_out(db.execute("SELECT * FROM listings WHERE id=?", (d["listing_id"],)).fetchone(),
                                include_photo=False)
    d["buyer"] = public_user(db.execute("SELECT * FROM users WHERE id=?", (d["buyer_id"],)).fetchone())
    d["seller"] = public_user(db.execute("SELECT * FROM users WHERE id=?", (d["seller_id"],)).fetchone())
    d["events"] = [_row(e) for e in db.execute(
        """SELECT e.*, u.display_name AS actor_name FROM transaction_events e
           LEFT JOIN users u ON u.id=e.actor_id WHERE transaction_id=? ORDER BY e.id""", (d["id"],)).fetchall()]
    if d["escrow_account_id"]:
        try:
            d["escrow_account"] = current_app.extensions["nessie"].get_account(d["escrow_account_id"])
        except NessieError as e:
            d["escrow_account"] = {"error": str(e)}
    d["my_rating"] = _row(db.execute(
        "SELECT stars, comment FROM ratings WHERE transaction_id=? AND rater_id=?", (d["id"], viewer_id)).fetchone())
    return d


@api.get("/transactions")
@login_required
def my_transactions():
    uid = current_user()["id"]
    rows = get_db().execute(
        "SELECT * FROM transactions WHERE buyer_id=? OR seller_id=? ORDER BY created_at DESC", (uid, uid)
    ).fetchall()
    return jsonify(transactions=[_txn_out(r, uid) for r in rows])


@api.get("/transactions/<int:tid>")
@login_required
def transaction(tid):
    u = current_user()
    t = escrow.get_txn(tid)
    if u["id"] not in (t["buyer_id"], t["seller_id"]) and not u["is_admin"]:
        return jsonify(error="Not your transaction"), 403
    return jsonify(transaction=_txn_out(t, u["id"]))


@api.post("/listings/<int:lid>/buy")
@verified_required
def buy(lid):
    tid = escrow.start(lid, current_user()["id"])
    return jsonify(transaction=_txn_out(escrow.get_txn(tid), current_user()["id"])), 201


@api.post("/transactions/<int:tid>/fund")
@verified_required
def fund(tid):
    code = escrow.fund(tid, current_user()["id"])
    out = _txn_out(escrow.get_txn(tid), current_user()["id"])
    out["handoff_code"] = code  # shown once to the buyer; never stored in plaintext
    return jsonify(transaction=out, handoff_code=code)


@api.post("/transactions/<int:tid>/handoff")
@verified_required
def handoff(tid):
    escrow.handoff(tid, current_user()["id"], _body().get("code"))
    return jsonify(transaction=_txn_out(escrow.get_txn(tid), current_user()["id"]))


@api.post("/transactions/<int:tid>/confirm")
@verified_required
def confirm(tid):
    escrow.confirm(tid, current_user()["id"])
    return jsonify(transaction=_txn_out(escrow.get_txn(tid), current_user()["id"]))


@api.post("/transactions/<int:tid>/cancel")
@login_required
def cancel(tid):
    escrow.cancel(tid, current_user()["id"])
    return jsonify(transaction=_txn_out(escrow.get_txn(tid), current_user()["id"]))


@api.post("/transactions/<int:tid>/dispute")
@login_required
def dispute(tid):
    escrow.dispute(tid, current_user()["id"], _body().get("reason"))
    return jsonify(transaction=_txn_out(escrow.get_txn(tid), current_user()["id"]))


@api.post("/transactions/<int:tid>/rate")
@login_required
def rate(tid):
    b = _body()
    escrow.rate(tid, current_user()["id"], b.get("stars"), b.get("comment"))
    return jsonify(transaction=_txn_out(escrow.get_txn(tid), current_user()["id"]))


# --------------------------------------------------------------------------
# In-app chat (per transaction)
# --------------------------------------------------------------------------
@api.get("/transactions/<int:tid>/messages")
@login_required
def messages(tid):
    u = current_user()
    t = escrow.get_txn(tid)
    if u["id"] not in (t["buyer_id"], t["seller_id"]) and not u["is_admin"]:
        return jsonify(error="Not your transaction"), 403
    rows = get_db().execute(
        """SELECT m.*, u.display_name AS sender_name FROM messages m JOIN users u ON u.id=m.sender_id
           WHERE transaction_id=? ORDER BY m.id""", (tid,)).fetchall()
    return jsonify(messages=[_row(r) for r in rows])


@api.post("/transactions/<int:tid>/messages")
@login_required
def send_message(tid):
    u = current_user()
    t = escrow.get_txn(tid)
    if u["id"] not in (t["buyer_id"], t["seller_id"]):
        return jsonify(error="Not your transaction"), 403
    body = (_body().get("body") or "").strip()
    if not body:
        return jsonify(error="Empty message"), 400
    flagged, reason = safety.inspect(body)
    get_db().execute(
        "INSERT INTO messages (transaction_id, sender_id, body, flagged, flag_reason) VALUES (?,?,?,?,?)",
        (tid, u["id"], body[:2000], int(flagged), reason),
    )
    get_db().commit()
    return jsonify(ok=True, flagged=flagged, flag_reason=reason), 201


# --------------------------------------------------------------------------
# Admin
# --------------------------------------------------------------------------
@api.get("/admin/disputes")
@admin_required
def admin_disputes():
    uid = current_user()["id"]
    rows = get_db().execute("SELECT * FROM transactions WHERE state='disputed' ORDER BY created_at").fetchall()
    out = []
    for r in rows:
        d = _txn_out(r, uid)
        d["messages"] = [_row(m) for m in get_db().execute(
            """SELECT m.*, u.display_name AS sender_name FROM messages m JOIN users u ON u.id=m.sender_id
               WHERE transaction_id=? ORDER BY m.id""", (r["id"],)).fetchall()]
        d["listing_photo"] = get_db().execute(
            "SELECT photo_data FROM listings WHERE id=?", (r["listing_id"],)).fetchone()["photo_data"]
        out.append(d)
    return jsonify(disputes=out)


@api.post("/admin/transactions/<int:tid>/resolve")
@admin_required
def admin_resolve(tid):
    b = _body()
    escrow.resolve(tid, current_user()["id"], b.get("outcome"), b.get("note") or "")
    return jsonify(transaction=_txn_out(escrow.get_txn(tid), current_user()["id"]))


@api.post("/admin/sweep")
@admin_required
def admin_sweep():
    return jsonify(acted=escrow.sweep())


@api.get("/admin/overview")
@admin_required
def admin_overview():
    db = get_db()
    counts = {r["state"]: r["n"] for r in db.execute(
        "SELECT state, COUNT(*) AS n FROM transactions GROUP BY state").fetchall()}
    users = db.execute("SELECT COUNT(*) AS n, SUM(verified) AS v FROM users").fetchone()
    flagged = db.execute("SELECT COUNT(*) AS n FROM messages WHERE flagged=1").fetchone()["n"]
    return jsonify(transaction_states=counts, users=users["n"], verified_users=users["v"] or 0,
                   flagged_messages=flagged)
