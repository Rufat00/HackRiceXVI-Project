"""Thin sqlite3 layer. No ORM: the schema is small and judges can read it."""
import sqlite3
from flask import current_app, g

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    display_name TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    -- Identity (from Persona). One human = one account.
    verified INTEGER NOT NULL DEFAULT 0,
    verified_at TEXT,
    persona_inquiry_id TEXT UNIQUE,
    persona_reference_id TEXT,           -- stable identity fingerprint
    legal_first_name TEXT,
    legal_last_name TEXT,
    birthdate TEXT,
    -- Money (Nessie)
    nessie_customer_id TEXT,
    nessie_account_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- One verified identity may only back one account. Enforced at the DB level.
CREATE UNIQUE INDEX IF NOT EXISTS users_identity_unique
    ON users(persona_reference_id) WHERE persona_reference_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seller_id INTEGER NOT NULL REFERENCES users(id),
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL CHECK (category IN ('item','ticket','sublet')),
    price_cents INTEGER NOT NULL CHECK (price_cents > 0),
    -- Proof of possession: the app assigns a random word at creation; the
    -- seller must photograph the item with that word visible, in-app.
    proof_word TEXT NOT NULL,
    photo_data TEXT,                      -- data URL captured in-app (not uploaded)
    photo_captured_at TEXT,
    -- Sublets only
    address TEXT,
    address_verified INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','active','reserved','sold','removed')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER NOT NULL REFERENCES listings(id),
    buyer_id INTEGER NOT NULL REFERENCES users(id),
    seller_id INTEGER NOT NULL REFERENCES users(id),
    amount_cents INTEGER NOT NULL,
    state TEXT NOT NULL DEFAULT 'created'
        CHECK (state IN ('created','funded','handed_off','released','refunded','disputed','cancelled')),
    -- Escrow account in Nessie, one per transaction, so the demo can show
    -- the money physically sitting somewhere neither party controls.
    escrow_account_id TEXT,
    fund_transfer_id TEXT,
    release_transfer_id TEXT,
    -- Handoff code shown to the buyer, typed by the seller in person.
    handoff_code_hash TEXT,
    funded_at TEXT,
    handoff_deadline TEXT,
    handed_off_at TEXT,
    confirm_deadline TEXT,
    closed_at TEXT,
    dispute_reason TEXT,
    dispute_opened_by INTEGER REFERENCES users(id),
    resolution TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS transaction_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id INTEGER NOT NULL REFERENCES transactions(id),
    actor_id INTEGER REFERENCES users(id),   -- NULL = system
    kind TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id INTEGER NOT NULL REFERENCES transactions(id),
    sender_id INTEGER NOT NULL REFERENCES users(id),
    body TEXT NOT NULL,
    flagged INTEGER NOT NULL DEFAULT 0,   -- off-platform payment pressure
    flag_reason TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Reputation accrues only from released transactions between two distinct
-- verified identities, so it cannot be farmed with sockpuppets.
CREATE TABLE IF NOT EXISTS ratings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id INTEGER NOT NULL REFERENCES transactions(id),
    rater_id INTEGER NOT NULL REFERENCES users(id),
    ratee_id INTEGER NOT NULL REFERENCES users(id),
    stars INTEGER NOT NULL CHECK (stars BETWEEN 1 AND 5),
    comment TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(transaction_id, rater_id)
);
"""


def get_db():
    if "db" not in g:
        conn = sqlite3.connect(current_app.config["DATABASE_PATH"], detect_types=0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        g.db = conn
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db(app):
    with app.app_context():
        conn = sqlite3.connect(app.config["DATABASE_PATH"])
        conn.executescript(SCHEMA)
        conn.commit()
        conn.close()
    app.teardown_appcontext(close_db)


def row_to_dict(row):
    return dict(row) if row is not None else None
