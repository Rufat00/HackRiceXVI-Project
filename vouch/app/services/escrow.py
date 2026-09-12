"""Escrow state machine.

    created ──fund──▶ funded ──handoff code──▶ handed_off ──confirm/timer──▶ released
       │                │                          │
       └─cancel─▶ cancelled                        ├──dispute──▶ disputed ──admin──▶ released | refunded
                        └──seller misses deadline─▶ refunded

Rules that stop the common scams:
  * Money only ever moves buyer -> escrow -> seller (or back to buyer).
    Nobody is asked to hand over an item until funds are confirmed locked.
  * The handoff code lives with the buyer and is typed by the seller. Money
    cannot advance unless the two people were physically together.
  * If the seller never hands off, the buyer is auto-refunded.
  * If the buyer goes silent after handoff, the seller is auto-paid.
  * A dispute freezes everything until an admin resolves it.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from flask import current_app, g

from ..db import get_db


class EscrowError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _parse(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def _hash_code(code):
    return hashlib.sha256(code.encode()).hexdigest()


def _nessie():
    return current_app.extensions["nessie"]


def _log(txn_id, kind, detail=None, actor_id=None):
    get_db().execute(
        "INSERT INTO transaction_events (transaction_id, actor_id, kind, detail) VALUES (?,?,?,?)",
        (txn_id, actor_id, kind, detail),
    )


def get_txn(txn_id):
    row = get_db().execute("SELECT * FROM transactions WHERE id=?", (txn_id,)).fetchone()
    if not row:
        raise EscrowError("Transaction not found", 404)
    return row


# --------------------------------------------------------------------------
# Platform escrow customer (owns every per-transaction escrow account)
# --------------------------------------------------------------------------
_PLATFORM_KEY = "platform_customer_id"


def platform_customer_id():
    db = get_db()
    db.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")
    row = db.execute("SELECT v FROM kv WHERE k=?", (_PLATFORM_KEY,)).fetchone()
    if row:
        return row["v"]
    cid = _nessie().create_customer("Vouch", "Escrow")
    db.execute("INSERT INTO kv (k,v) VALUES (?,?)", (_PLATFORM_KEY, cid))
    db.commit()
    return cid


# --------------------------------------------------------------------------
# Transitions
# --------------------------------------------------------------------------
def start(listing_id, buyer_id):
    db = get_db()
    listing = db.execute("SELECT * FROM listings WHERE id=?", (listing_id,)).fetchone()
    if not listing or listing["status"] != "active":
        raise EscrowError("This listing is no longer available")
    if listing["seller_id"] == buyer_id:
        raise EscrowError("You can't buy your own listing")
    buyer = db.execute("SELECT * FROM users WHERE id=?", (buyer_id,)).fetchone()
    if not buyer["verified"]:
        raise EscrowError("Verify your identity before buying", 403)

    cur = db.execute(
        "INSERT INTO transactions (listing_id, buyer_id, seller_id, amount_cents) VALUES (?,?,?,?)",
        (listing_id, buyer_id, listing["seller_id"], listing["price_cents"]),
    )
    txn_id = cur.lastrowid
    db.execute("UPDATE listings SET status='reserved' WHERE id=?", (listing_id,))
    _log(txn_id, "created", f"{buyer['display_name']} started a purchase", buyer_id)
    db.commit()
    return txn_id


def fund(txn_id, buyer_id):
    """Buyer -> per-transaction escrow account. Generates the handoff code."""
    db = get_db()
    txn = get_txn(txn_id)
    if txn["buyer_id"] != buyer_id:
        raise EscrowError("Only the buyer can fund this", 403)
    if txn["state"] != "created":
        raise EscrowError(f"Can't fund from state '{txn['state']}'")

    buyer = db.execute("SELECT * FROM users WHERE id=?", (buyer_id,)).fetchone()
    if not buyer["nessie_account_id"]:
        raise EscrowError("Your wallet isn't set up yet")

    nessie = _nessie()
    bal = nessie.get_account(buyer["nessie_account_id"])["balance_cents"]
    if bal < txn["amount_cents"]:
        raise EscrowError(f"Insufficient funds: balance ${bal/100:.2f}, need ${txn['amount_cents']/100:.2f}")

    escrow_id = nessie.create_account(platform_customer_id(), f"Escrow txn #{txn_id}")
    xfer_id = nessie.transfer(buyer["nessie_account_id"], escrow_id, txn["amount_cents"],
                              f"Vouch escrow funding txn #{txn_id}")

    code = f"{secrets.randbelow(10**6):06d}"
    now = _now()
    deadline = now + timedelta(seconds=current_app.config["HANDOFF_DEADLINE_SECONDS"])
    db.execute(
        """UPDATE transactions SET state='funded', escrow_account_id=?, fund_transfer_id=?,
           handoff_code_hash=?, funded_at=?, handoff_deadline=? WHERE id=?""",
        (escrow_id, xfer_id, _hash_code(code), _iso(now), _iso(deadline), txn_id),
    )
    _log(txn_id, "funded", f"${txn['amount_cents']/100:.2f} moved into escrow account {escrow_id}", buyer_id)
    db.commit()
    # The plaintext code is returned exactly once, to the buyer, and never stored.
    g.setdefault("fresh_codes", {})[txn_id] = code
    return code


def handoff(txn_id, seller_id, code):
    """Seller types the buyer's code in person. Proves both were present."""
    db = get_db()
    txn = get_txn(txn_id)
    if txn["seller_id"] != seller_id:
        raise EscrowError("Only the seller can confirm a handoff", 403)
    if txn["state"] != "funded":
        raise EscrowError(f"Can't hand off from state '{txn['state']}'")
    code = (code or "").strip()
    if not code or _hash_code(code) != txn["handoff_code_hash"]:
        _log(txn_id, "handoff_failed", "Wrong handoff code entered", seller_id)
        db.commit()
        raise EscrowError("That code doesn't match. Ask the buyer to show you theirs.")

    now = _now()
    window = now + timedelta(seconds=current_app.config["CONFIRM_WINDOW_SECONDS"])
    db.execute(
        "UPDATE transactions SET state='handed_off', handed_off_at=?, confirm_deadline=? WHERE id=?",
        (_iso(now), _iso(window), txn_id),
    )
    _log(txn_id, "handed_off", "Handoff code accepted; buyer's confirmation window started", seller_id)
    db.commit()


def _release(txn_id, txn, actor_id, reason):
    db = get_db()
    seller = db.execute("SELECT * FROM users WHERE id=?", (txn["seller_id"],)).fetchone()
    xfer = _nessie().transfer(txn["escrow_account_id"], seller["nessie_account_id"],
                              txn["amount_cents"], f"Vouch release txn #{txn_id}")
    db.execute(
        "UPDATE transactions SET state='released', release_transfer_id=?, closed_at=?, resolution=? WHERE id=?",
        (xfer, _iso(_now()), reason, txn_id),
    )
    db.execute("UPDATE listings SET status='sold' WHERE id=?", (txn["listing_id"],))
    _log(txn_id, "released", f"${txn['amount_cents']/100:.2f} released to seller ({reason})", actor_id)


def _refund(txn_id, txn, actor_id, reason):
    db = get_db()
    buyer = db.execute("SELECT * FROM users WHERE id=?", (txn["buyer_id"],)).fetchone()
    xfer = _nessie().transfer(txn["escrow_account_id"], buyer["nessie_account_id"],
                              txn["amount_cents"], f"Vouch refund txn #{txn_id}")
    db.execute(
        "UPDATE transactions SET state='refunded', release_transfer_id=?, closed_at=?, resolution=? WHERE id=?",
        (xfer, _iso(_now()), reason, txn_id),
    )
    db.execute("UPDATE listings SET status='active' WHERE id=?", (txn["listing_id"],))
    _log(txn_id, "refunded", f"${txn['amount_cents']/100:.2f} refunded to buyer ({reason})", actor_id)


def confirm(txn_id, buyer_id):
    txn = get_txn(txn_id)
    if txn["buyer_id"] != buyer_id:
        raise EscrowError("Only the buyer can confirm receipt", 403)
    if txn["state"] != "handed_off":
        raise EscrowError(f"Can't confirm from state '{txn['state']}'")
    _release(txn_id, txn, buyer_id, "buyer confirmed")
    get_db().commit()


def cancel(txn_id, user_id):
    db = get_db()
    txn = get_txn(txn_id)
    if user_id not in (txn["buyer_id"], txn["seller_id"]):
        raise EscrowError("Not your transaction", 403)
    if txn["state"] != "created":
        raise EscrowError("Only unfunded purchases can be cancelled")
    db.execute("UPDATE transactions SET state='cancelled', closed_at=? WHERE id=?", (_iso(_now()), txn_id))
    db.execute("UPDATE listings SET status='active' WHERE id=?", (txn["listing_id"],))
    _log(txn_id, "cancelled", None, user_id)
    db.commit()


def dispute(txn_id, user_id, reason):
    db = get_db()
    txn = get_txn(txn_id)
    if user_id not in (txn["buyer_id"], txn["seller_id"]):
        raise EscrowError("Not your transaction", 403)
    if txn["state"] not in ("funded", "handed_off"):
        raise EscrowError("Disputes can only be opened while money is in escrow")
    reason = (reason or "").strip()
    if len(reason) < 10:
        raise EscrowError("Tell us what went wrong (at least a sentence)")
    db.execute(
        "UPDATE transactions SET state='disputed', dispute_reason=?, dispute_opened_by=? WHERE id=?",
        (reason, user_id, txn_id),
    )
    _log(txn_id, "disputed", reason, user_id)
    db.commit()


def resolve(txn_id, admin_id, outcome, note=""):
    """Admin decision. outcome: 'release' (pay seller) or 'refund' (pay buyer)."""
    txn = get_txn(txn_id)
    if txn["state"] != "disputed":
        raise EscrowError("Only disputed transactions can be resolved")
    reason = f"admin decision: {outcome}" + (f" — {note.strip()}" if note.strip() else "")
    if outcome == "release":
        _release(txn_id, txn, admin_id, reason)
    elif outcome == "refund":
        _refund(txn_id, txn, admin_id, reason)
    else:
        raise EscrowError("outcome must be 'release' or 'refund'")
    get_db().commit()


# --------------------------------------------------------------------------
# Timers. Called lazily on every API request and via POST /api/admin/sweep,
# so no background worker is needed for the demo.
# --------------------------------------------------------------------------
def sweep():
    db = get_db()
    now = _now()
    acted = []
    for txn in db.execute("SELECT * FROM transactions WHERE state IN ('funded','handed_off')").fetchall():
        if txn["state"] == "funded" and txn["handoff_deadline"] and _parse(txn["handoff_deadline"]) <= now:
            _refund(txn["id"], txn, None, "seller missed handoff deadline")
            acted.append((txn["id"], "refunded"))
        elif txn["state"] == "handed_off" and txn["confirm_deadline"] and _parse(txn["confirm_deadline"]) <= now:
            _release(txn["id"], txn, None, "buyer confirmation window elapsed")
            acted.append((txn["id"], "released"))
    if acted:
        db.commit()
    return acted


# --------------------------------------------------------------------------
# Reputation: only from released transactions between distinct identities
# --------------------------------------------------------------------------
def rate(txn_id, rater_id, stars, comment=""):
    db = get_db()
    txn = get_txn(txn_id)
    if txn["state"] != "released":
        raise EscrowError("You can only rate a completed transaction")
    if rater_id == txn["buyer_id"]:
        ratee_id = txn["seller_id"]
    elif rater_id == txn["seller_id"]:
        ratee_id = txn["buyer_id"]
    else:
        raise EscrowError("Not your transaction", 403)
    a = db.execute("SELECT persona_reference_id FROM users WHERE id=?", (rater_id,)).fetchone()
    b = db.execute("SELECT persona_reference_id FROM users WHERE id=?", (ratee_id,)).fetchone()
    if a["persona_reference_id"] and a["persona_reference_id"] == b["persona_reference_id"]:
        raise EscrowError("Same identity on both sides; rating rejected", 403)
    try:
        db.execute(
            "INSERT INTO ratings (transaction_id, rater_id, ratee_id, stars, comment) VALUES (?,?,?,?,?)",
            (txn_id, rater_id, ratee_id, int(stars), (comment or "").strip()[:500]),
        )
    except Exception:
        raise EscrowError("You've already rated this transaction")
    db.commit()


def reputation(user_id):
    row = get_db().execute(
        "SELECT COUNT(*) AS n, AVG(stars) AS avg FROM ratings WHERE ratee_id=?", (user_id,)
    ).fetchone()
    completed = get_db().execute(
        "SELECT COUNT(*) AS n FROM transactions WHERE state='released' AND (buyer_id=? OR seller_id=?)",
        (user_id, user_id),
    ).fetchone()["n"]
    return {"ratings": row["n"], "avg_stars": round(row["avg"], 2) if row["avg"] else None,
            "completed_transactions": completed}
