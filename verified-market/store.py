"""
SQLite persistence + the escrow state machine.

Deal states:
  funded     buyer's money moved into a per-deal escrow account
  handed_off seller entered the buyer's one-time handoff code (both were present)
  released   buyer confirmed (or window expired) -> escrow -> seller
  disputed   either side flagged; money frozen until admin resolves
  refunded   admin resolved for buyer -> escrow -> buyer

Legal transitions are enforced in `transition()`; nothing else mutates state.
"""
import os
import time
import uuid
import sqlite3
import secrets

DB_PATH = os.environ.get("DB_PATH", "market.db")

# Buyer has this long after handoff to confirm or dispute before auto-release.
CONFIRM_WINDOW_SECONDS = int(os.environ.get("CONFIRM_WINDOW_SECONDS", 24 * 3600))
# Seller has this long after funding to hand off before auto-refund.
HANDOFF_WINDOW_SECONDS = int(os.environ.get("HANDOFF_WINDOW_SECONDS", 7 * 24 * 3600))

TRANSITIONS = {
    "funded":     {"handed_off", "disputed", "refunded"},
    "handed_off": {"released", "disputed"},
    "disputed":   {"released", "refunded"},
    "released":   set(),
    "refunded":   set(),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY, name TEXT, email TEXT UNIQUE,
  verified INTEGER DEFAULT 0, inquiry_id TEXT, verified_name TEXT,
  nessie_customer TEXT, nessie_account TEXT,
  is_admin INTEGER DEFAULT 0, banned INTEGER DEFAULT 0, created REAL
);
CREATE TABLE IF NOT EXISTS listings (
  id TEXT PRIMARY KEY, seller_id TEXT, title TEXT, category TEXT,
  price REAL, description TEXT, photo TEXT, proof_word TEXT,
  status TEXT DEFAULT 'open', created REAL
);
CREATE TABLE IF NOT EXISTS deals (
  id TEXT PRIMARY KEY, listing_id TEXT, buyer_id TEXT, seller_id TEXT,
  amount REAL, escrow_account TEXT, state TEXT, handoff_code TEXT,
  funded_at REAL, handed_off_at REAL, closed_at REAL,
  dispute_reason TEXT, dispute_by TEXT
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, deal_id TEXT, at REAL,
  actor TEXT, kind TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS reviews (
  id TEXT PRIMARY KEY, deal_id TEXT UNIQUE, reviewer_id TEXT,
  reviewee_id TEXT, rating INTEGER, text TEXT, created REAL
);
"""


def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    with conn() as c:
        c.executescript(SCHEMA)


def new_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def row(r):
    return dict(r) if r is not None else None


# ------------------------------------------------------------------- events
def log(c, deal_id, actor, kind, detail=""):
    c.execute("INSERT INTO events (deal_id, at, actor, kind, detail) VALUES (?,?,?,?,?)",
              (deal_id, time.time(), actor, kind, detail))


def transition(c, deal, to_state, actor, detail=""):
    """Only way to change a deal's state. Raises on illegal moves."""
    frm = deal["state"]
    if to_state not in TRANSITIONS.get(frm, set()):
        raise ValueError(f"Cannot go from {frm} to {to_state}")
    now = time.time()
    stamp = {"handed_off": "handed_off_at", "released": "closed_at",
             "refunded": "closed_at"}.get(to_state)
    if stamp:
        c.execute(f"UPDATE deals SET state=?, {stamp}=? WHERE id=?",
                  (to_state, now, deal["id"]))
    else:
        c.execute("UPDATE deals SET state=? WHERE id=?", (to_state, deal["id"]))
    log(c, deal["id"], actor, to_state, detail)
    deal = dict(deal)
    deal["state"] = to_state
    return deal


def make_handoff_code():
    # 6 digits, shown to buyer, typed by seller in person.
    return f"{secrets.randbelow(10**6):06d}"


def proof_word():
    # Random word the seller must write on paper in the listing photo.
    words = ["OWL", "LEMON", "TANGO", "BRICK", "COMET", "DELTA", "MAPLE",
             "PIXEL", "RIVER", "SOLAR", "TIGER", "VIOLET", "ZEBRA", "QUARTZ"]
    return f"{secrets.choice(words)}-{secrets.randbelow(100):02d}"


def overdue_deals(c):
    """Deals whose windows have expired and should auto-resolve."""
    now = time.time()
    auto_release = c.execute(
        "SELECT * FROM deals WHERE state='handed_off' AND handed_off_at < ?",
        (now - CONFIRM_WINDOW_SECONDS,)).fetchall()
    auto_refund = c.execute(
        "SELECT * FROM deals WHERE state='funded' AND funded_at < ?",
        (now - HANDOFF_WINDOW_SECONDS,)).fetchall()
    return [row(d) for d in auto_release], [row(d) for d in auto_refund]
