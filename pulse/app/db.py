import sqlite3
from flask import current_app, g

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS parties (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,             -- 4-letter join code, in the QR
    name TEXT NOT NULL,
    host_token TEXT UNIQUE NOT NULL,       -- host's secret (cookie)
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    ends_at TEXT,                          -- optional planned end; drives the energy arc
    pinned_theme_id INTEGER,               -- host override of the crowd's theme
    auto_dj INTEGER NOT NULL DEFAULT 1,
    settings TEXT NOT NULL DEFAULT '{}',   -- JSON
    spotify_tokens TEXT                    -- JSON {access,refresh,expires_at}
);

CREATE TABLE IF NOT EXISTS guests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    party_id INTEGER NOT NULL REFERENCES parties(id),
    token TEXT UNIQUE NOT NULL,            -- cookie; no login for guests
    nickname TEXT NOT NULL,
    joined_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen TEXT NOT NULL DEFAULT (datetime('now')),
    is_host INTEGER NOT NULL DEFAULT 0
);

-- Track metadata, cached from Spotify or from the mock catalog.
CREATE TABLE IF NOT EXISTS tracks (
    id TEXT PRIMARY KEY,                   -- spotify:track:... or mock:...
    title TEXT NOT NULL,
    artist TEXT NOT NULL,
    album TEXT,
    year INTEGER,
    duration_ms INTEGER NOT NULL,
    genres TEXT NOT NULL DEFAULT '[]',     -- JSON list (artist genres from Spotify, or catalog tags)
    energy REAL, danceability REAL, valence REAL, tempo REAL,  -- 0..1 except tempo (bpm); NULL if unknown
    popularity INTEGER,
    art_url TEXT,
    preview_url TEXT,
    explicit INTEGER NOT NULL DEFAULT 0
);

-- A guest suggesting a track for a party. Many guests can suggest the same track;
-- each row is one guest's ask. The DJ groups by track.
CREATE TABLE IF NOT EXISTS suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    party_id INTEGER NOT NULL REFERENCES parties(id),
    track_id TEXT NOT NULL REFERENCES tracks(id),
    guest_id INTEGER NOT NULL REFERENCES guests(id),
    note TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(party_id, track_id, guest_id)
);

-- Guests can +1/-1 a queued suggestion (before it plays).
CREATE TABLE IF NOT EXISTS suggestion_votes (
    party_id INTEGER NOT NULL REFERENCES parties(id),
    track_id TEXT NOT NULL REFERENCES tracks(id),
    guest_id INTEGER NOT NULL REFERENCES guests(id),
    value INTEGER NOT NULL CHECK (value IN (-1, 1)),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (party_id, track_id, guest_id)
);

-- Every time something plays. Votes on the *playing* song attach here.
CREATE TABLE IF NOT EXISTS plays (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    party_id INTEGER NOT NULL REFERENCES parties(id),
    track_id TEXT NOT NULL REFERENCES tracks(id),
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at TEXT,
    end_reason TEXT,                       -- finished | skipped_by_crowd | skipped_by_host | replaced
    chosen_by TEXT NOT NULL DEFAULT 'dj',  -- dj | host
    score REAL,
    explanation TEXT                       -- JSON of score components
);

CREATE TABLE IF NOT EXISTS play_votes (
    play_id INTEGER NOT NULL REFERENCES plays(id),
    guest_id INTEGER NOT NULL REFERENCES guests(id),
    value INTEGER NOT NULL CHECK (value IN (-1, 1)),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (play_id, guest_id)
);

CREATE TABLE IF NOT EXISTS themes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    party_id INTEGER NOT NULL REFERENCES parties(id),
    guest_id INTEGER NOT NULL REFERENCES guests(id),
    text TEXT NOT NULL,
    keywords TEXT NOT NULL DEFAULT '[]',   -- JSON, parsed from text (or by Claude)
    year_from INTEGER, year_to INTEGER,
    target_energy REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(party_id, text)
);

CREATE TABLE IF NOT EXISTS theme_votes (
    theme_id INTEGER NOT NULL REFERENCES themes(id),
    guest_id INTEGER NOT NULL REFERENCES guests(id),
    value INTEGER NOT NULL CHECK (value IN (-1, 1)),
    PRIMARY KEY (theme_id, guest_id)
);
"""


def get_db():
    if "db" not in g:
        conn = sqlite3.connect(current_app.config["DATABASE_PATH"])
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        g.db = conn
    return g.db


def close_db(_e=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db(app):
    conn = sqlite3.connect(app.config["DATABASE_PATH"])
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    app.teardown_appcontext(close_db)
