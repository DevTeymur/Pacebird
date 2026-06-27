"""
core/db.py — SQLite token store for multi-user support.

One row per athlete. Tokens are refreshed automatically before any
Strava API call when expires_at is within 5 minutes of expiry.

Schema
------
tokens (
    athlete_id   INTEGER PRIMARY KEY,
    access_token  TEXT NOT NULL,
    refresh_token TEXT NOT NULL,
    expires_at    INTEGER NOT NULL,   -- Unix timestamp from Strava
    athlete_json  TEXT NOT NULL       -- JSON blob of the athlete object
)
"""

import os
import json
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "pacebird.db")


def _conn():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    """Create the tokens table if it doesn't exist. Call once at startup."""
    with _conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS tokens (
                athlete_id    INTEGER PRIMARY KEY,
                access_token  TEXT    NOT NULL,
                refresh_token TEXT    NOT NULL,
                expires_at    INTEGER NOT NULL,
                athlete_json  TEXT    NOT NULL
            )
        """)
        con.commit()


def upsert_token(athlete_id, access_token, refresh_token, expires_at, athlete_obj):
    """Insert or update a token row for this athlete."""
    with _conn() as con:
        con.execute("""
            INSERT INTO tokens (athlete_id, access_token, refresh_token, expires_at, athlete_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(athlete_id) DO UPDATE SET
                access_token  = excluded.access_token,
                refresh_token = excluded.refresh_token,
                expires_at    = excluded.expires_at,
                athlete_json  = excluded.athlete_json
        """, (
            athlete_id,
            access_token,
            refresh_token,
            int(expires_at),
            json.dumps(athlete_obj),
        ))
        con.commit()


def get_token(athlete_id):
    """Return the token row for this athlete, or None."""
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM tokens WHERE athlete_id = ?", (athlete_id,)
        ).fetchone()
    if row is None:
        return None
    return dict(row)


def delete_token(athlete_id):
    """Remove token row — called on logout."""
    with _conn() as con:
        con.execute("DELETE FROM tokens WHERE athlete_id = ?", (athlete_id,))
        con.commit()
