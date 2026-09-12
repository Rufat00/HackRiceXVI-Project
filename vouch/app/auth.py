from functools import wraps

from flask import current_app, g, jsonify, session

from .db import get_db


def current_user():
    if "user" in g:
        return g.user
    uid = session.get("uid")
    g.user = get_db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone() if uid else None
    return g.user


def login_required(f):
    @wraps(f)
    def inner(*a, **kw):
        if not current_user():
            return jsonify(error="Sign in to continue"), 401
        return f(*a, **kw)
    return inner


def verified_required(f):
    @wraps(f)
    def inner(*a, **kw):
        u = current_user()
        if not u:
            return jsonify(error="Sign in to continue"), 401
        if not u["verified"]:
            return jsonify(error="Verify your identity first", code="unverified"), 403
        return f(*a, **kw)
    return inner


def admin_required(f):
    @wraps(f)
    def inner(*a, **kw):
        u = current_user()
        if not u:
            return jsonify(error="Sign in to continue"), 401
        if not u["is_admin"]:
            return jsonify(error="Admins only"), 403
        return f(*a, **kw)
    return inner


def public_user(row, include_private=False):
    """What other users may see about a person. Legal name and DOB never leave
    the server; verification is exposed as a boolean plus first-name display."""
    if row is None:
        return None
    d = {
        "id": row["id"],
        "display_name": row["display_name"],
        "verified": bool(row["verified"]),
        "verified_at": row["verified_at"],
        "member_since": row["created_at"],
    }
    if include_private:
        d.update({
            "email": row["email"],
            "is_admin": bool(row["is_admin"]),
            "legal_first_name": row["legal_first_name"],
            "nessie_account_id": row["nessie_account_id"],
        })
    return d


def is_admin_email(email):
    admins = current_app.config["ADMIN_EMAILS"]
    return email.lower() in admins
