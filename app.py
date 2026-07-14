"""Minimal cloud backup/restore server for MediSuite installs.

Each MediSuite install can optionally push an *already client-side encrypted*
database backup here, and later pull it back down onto a replacement PC. This
server never sees plaintext - it only ever stores/returns whatever encrypted
bytes the client sends. Backed by Postgres (point it at a free Neon project
via DATABASE_URL - Neon's free tier does not expire, unlike Render's own free
Postgres which is deleted after 30+14 days).

Run locally against a real Postgres for testing:
    DATABASE_URL=postgresql://user:pass@host/dbname python3 app.py

Deploy: see README.md in this folder.
"""

from __future__ import annotations

import os

import psycopg2
import psycopg2.extras
from flask import Flask, Response, g, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "")
# Max encrypted blob size accepted per backup (bytes). Keeps a single runaway
# upload from blowing through Neon's free-tier storage cap on its own.
MAX_BACKUP_BYTES = int(os.getenv("MAX_BACKUP_BYTES", str(200 * 1024 * 1024)))


def get_db():
    if "db" not in g:
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL is not configured")
        g.db = psycopg2.connect(DATABASE_URL)
        with g.db.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    id SERIAL PRIMARY KEY,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    salt TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS backups (
                    account_id INTEGER PRIMARY KEY REFERENCES accounts(id) ON DELETE CASCADE,
                    encrypted_blob BYTEA NOT NULL,
                    size INTEGER NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        g.db.commit()
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def _get_account_by_username(username: str):
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT id, username, password_hash, salt FROM accounts WHERE username = %s",
            (username,),
        )
        return cur.fetchone()


def require_account_auth(view):
    """Verify HTTP Basic Auth against the accounts table; attaches g.account."""

    def wrapped(*args, **kwargs):
        auth = request.authorization
        if not auth or not auth.username or not auth.password:
            return jsonify({"error": "authentication required"}), 401

        account = _get_account_by_username(auth.username)
        if not account or not check_password_hash(account["password_hash"], auth.password):
            return jsonify({"error": "invalid credentials"}), 401

        g.account = account
        return view(*args, **kwargs)

    wrapped.__name__ = view.__name__
    return wrapped


@app.route("/signup", methods=["POST"])
def signup():
    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    salt = (data.get("salt") or "").strip()

    if not username or not password or not salt:
        return jsonify({"error": "username, password, and salt are required"}), 400
    if len(password) < 8:
        return jsonify({"error": "password must be at least 8 characters"}), 400

    if _get_account_by_username(username):
        return jsonify({"error": "username already taken"}), 409

    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO accounts (username, password_hash, salt) VALUES (%s, %s, %s)",
            (username, generate_password_hash(password), salt),
        )
    db.commit()
    return jsonify({"ok": True}), 201


@app.route("/login", methods=["POST"])
@require_account_auth
def login():
    return jsonify({"ok": True, "username": g.account["username"], "salt": g.account["salt"]})


@app.route("/backup", methods=["PUT"])
@require_account_auth
def upload_backup():
    encrypted = request.get_data(cache=False)
    if not encrypted:
        return jsonify({"error": "empty backup body"}), 400
    if len(encrypted) > MAX_BACKUP_BYTES:
        return jsonify({"error": "backup too large"}), 413

    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO backups (account_id, encrypted_blob, size, updated_at)
            VALUES (%s, %s, %s, now())
            ON CONFLICT (account_id) DO UPDATE
            SET encrypted_blob = EXCLUDED.encrypted_blob, size = EXCLUDED.size, updated_at = now()
            """,
            (g.account["id"], psycopg2.Binary(encrypted), len(encrypted)),
        )
    db.commit()
    return jsonify({"ok": True, "size": len(encrypted)})


@app.route("/backup", methods=["GET"])
@require_account_auth
def download_backup():
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT encrypted_blob, updated_at FROM backups WHERE account_id = %s",
            (g.account["id"],),
        )
        row = cur.fetchone()

    if not row:
        return jsonify({"error": "no backup found for this account"}), 404

    response = Response(bytes(row["encrypted_blob"]), mimetype="application/octet-stream")
    response.headers["X-Backup-Salt"] = g.account["salt"]
    response.headers["X-Backup-Updated-At"] = row["updated_at"].isoformat()
    return response


@app.route("/status", methods=["GET"])
@require_account_auth
def backup_status():
    db = get_db()
    with db.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT size, updated_at FROM backups WHERE account_id = %s",
            (g.account["id"],),
        )
        row = cur.fetchone()

    if not row:
        return jsonify({"has_backup": False})
    return jsonify(
        {
            "has_backup": True,
            "size": row["size"],
            "updated_at": row["updated_at"].isoformat(),
            "salt": g.account["salt"],
        }
    )


@app.route("/health")
def health():
    return jsonify({"ok": True})


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8001"))
    app.run(host="0.0.0.0", port=port)
