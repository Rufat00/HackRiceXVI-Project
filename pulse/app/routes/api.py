import io
import json
import secrets
import string
from functools import wraps

from flask import Blueprint, current_app, g, jsonify, make_response, redirect, request, send_file

from ..db import get_db
from ..services import party as P
from ..services.dj import parse_theme
from ..services.spotify import SpotifyError
from ..services.real_test_tracks import seed_real_tracks

api = Blueprint("api", __name__, url_prefix="/api")

HOST_COOKIE, GUEST_COOKIE = "pulse_host", "pulse_guest"


def _body():
    return request.get_json(silent=True) or {}


@api.errorhandler(SpotifyError)
def _sp_err(e):
    return jsonify(error=f"Spotify: {e}"), 502


def _party_by_code(code):
    return get_db().execute("SELECT * FROM parties WHERE code=?", (code.upper(),)).fetchone()


def _guest_from_cookie(party):
    tok = request.cookies.get(GUEST_COOKIE)
    if not tok:
        return None
    r = get_db().execute("SELECT * FROM guests WHERE token=? AND party_id=?", (tok, party["id"])).fetchone()
    if r:
        get_db().execute("UPDATE guests SET last_seen=datetime('now') WHERE id=?", (r["id"],))
        get_db().commit()
    return r


def with_party(f):
    @wraps(f)
    def inner(code, *a, **kw):
        party = _party_by_code(code)
        if not party:
            return jsonify(error="No party with that code"), 404
        g.party, g.guest = party, _guest_from_cookie(party)
        return f(*a, **kw)
    return inner


def guest_required(f):
    @wraps(f)
    def inner(*a, **kw):
        if not g.guest:
            return jsonify(error="Join the party first", code="join"), 401
        return f(*a, **kw)
    return inner


def host_required(f):
    @wraps(f)
    def inner(*a, **kw):
        if request.cookies.get(HOST_COOKIE) != g.party["host_token"]:
            return jsonify(error="Host only"), 403
        return f(*a, **kw)
    return inner


def _set_cookie(resp, name, value):
    resp.set_cookie(name, value, max_age=60 * 60 * 24 * 2, httponly=True, samesite="Lax")
    return resp


# ---------------------------------------------------------------- party lifecycle
@api.post("/parties")
def create_party():
    b = _body()
    name = (b.get("name") or "House party").strip()[:60]
    db = get_db()
    for _ in range(20):
        code = "".join(secrets.choice(string.ascii_uppercase) for _ in range(4))
        if not db.execute("SELECT 1 FROM parties WHERE code=?", (code,)).fetchone():
            break
    host_token = secrets.token_urlsafe(24)
    hours = b.get("hours")
    ends = f"datetime('now','+{int(hours)} hours')" if hours else "NULL"
    cur = db.execute(f"INSERT INTO parties (code, name, host_token, ends_at) VALUES (?,?,?,{ends})", (code, name, host_token))
    pid = cur.lastrowid
    gtok = secrets.token_urlsafe(16)
    db.execute("INSERT INTO guests (party_id, token, nickname, is_host) VALUES (?,?,?,1)", (pid, gtok, (b.get("host_name") or "Host")[:24]))
    db.commit()
    resp = make_response(jsonify(code=code, host_url=f"/host/{code}", join_url=_join_url(code)))
    _set_cookie(resp, HOST_COOKIE, host_token)
    return _set_cookie(resp, GUEST_COOKIE, gtok), 201


def _join_url(code):
    base = current_app.config["PUBLIC_URL"].rstrip("/") or request.host_url.rstrip("/")
    return f"{base}/join/{code}"


@api.get("/parties/<code>/qr.png")
def qr(code):
    import qrcode
    img = qrcode.make(_join_url(code), box_size=10, border=1)
    buf = io.BytesIO(); img.save(buf, "PNG"); buf.seek(0)
    return send_file(buf, mimetype="image/png", max_age=300)


@api.post("/parties/<code>/join")
@with_party
def join():
    party = g.party
    nick = (_body().get("nickname") or "").strip()[:24]
    if g.guest:
        if nick:
            get_db().execute("UPDATE guests SET nickname=? WHERE id=?", (nick, g.guest["id"])); get_db().commit()
        return jsonify(ok=True, guest={"id": g.guest["id"], "nickname": nick or g.guest["nickname"]}, party=_party_public(party))
    if not nick:
        return jsonify(error="Pick a nickname"), 400
    tok = secrets.token_urlsafe(16)
    cur = get_db().execute("INSERT INTO guests (party_id, token, nickname) VALUES (?,?,?)", (party["id"], tok, nick))
    get_db().commit()
    resp = make_response(jsonify(ok=True, guest={"id": cur.lastrowid, "nickname": nick}, party=_party_public(party)))
    return _set_cookie(resp, GUEST_COOKIE, tok), 201


def _party_public(party):
    return {"code": party["code"], "name": party["name"], "started_at": party["started_at"], "auto_dj": bool(party["auto_dj"]),
            "spotify_live": P.spotify_live(party), "spotify_mock": current_app.config["SPOTIFY_MOCK"],
            "time_scale": current_app.config["MOCK_TIME_SCALE"] if current_app.config["SPOTIFY_MOCK"] else 1.0,
            "settings": json.loads(party["settings"] or "{}")}


# ---------------------------------------------------------------- shared state
@api.get("/parties/<code>/state")
@with_party
def state():
    party = g.party
    events = P.tick(party)
    party = _party_by_code(party["code"])  # reload after tick
    ctx = P.party_context(party)
    gid = g.guest["id"] if g.guest else None
    is_host = request.cookies.get(HOST_COOKIE) == party["host_token"]
    out = {
        "party": _party_public(party),
        "me": {"id": g.guest["id"], "nickname": g.guest["nickname"], "is_host": is_host} if g.guest else None,
        "now_playing": P.now_playing(party, gid),
        "queue": [_cand_public(c) for c in P.queue_view(party, ctx, gid)],
        "themes": P.themes_view(party, gid),
        "dj": {"target_energy": ctx["target_energy"], "phase": ctx["phase"], "elapsed_min": ctx["elapsed_min"],
               "active_theme": ctx["theme"]["text"] if ctx["theme"] else None,
               "affinity": dict(sorted(ctx["affinity"].items(), key=lambda kv: -abs(kv[1]))[:6])},
        "stats": P.stats(party),
        "history": P.history_view(party) if is_host else [],
        "events": events,
        "join_url": _join_url(party["code"]),
    }
    ai = current_app.extensions.get("ai")
    if is_host and ai and ai.enabled and out["now_playing"]:
        np_ = out["now_playing"]
        out["shoutout"] = _cached_shoutout(party, np_, ai, ctx)
    return jsonify(out)


_shoutouts = {}


def _cached_shoutout(party, np_, ai, ctx):
    key = np_["play_id"]
    if key not in _shoutouts:
        _shoutouts[key] = ai.shoutout(np_["track"], np_["explanation"], ctx["theme"]["text"] if ctx["theme"] else None, np_["up"], np_["down"]) or ""
    return _shoutouts[key]


def _cand_public(c):
    return {"track": c["track"], "score": c["score"], "components": c["components"], "explanation": c["explanation"],
            "n_suggesters": c["n_suggesters"], "net_votes": c["net_votes"], "source": c["source"], "my_vote": c.get("my_vote")}


# ---------------------------------------------------------------- guest actions
@api.get("/parties/<code>/search")
@with_party
@guest_required
def search():
    q = (request.args.get("q") or "").strip()
    return jsonify(tracks=P.search_tracks(g.party, q))


@api.post("/parties/<code>/suggest")
@with_party
@guest_required
def suggest():
    b = _body()
    tid = b.get("track_id")
    if not tid or not P.get_track(tid):
        return jsonify(error="Search for the song first"), 400
    db = get_db()
    n = db.execute("SELECT COUNT(*) AS n FROM suggestions WHERE party_id=? AND guest_id=? AND created_at > datetime('now','-1 hour')",
                   (g.party["id"], g.guest["id"])).fetchone()["n"]
    if n >= current_app.config["SUGGESTIONS_PER_GUEST_PER_HOUR"]:
        return jsonify(error="You've made a lot of requests this hour — let others get a turn"), 429
    cur = P.current_play(g.party["id"])
    if cur and cur["track_id"] == tid:
        return jsonify(error="That's playing right now"), 400
    try:
        db.execute("INSERT INTO suggestions (party_id, track_id, guest_id, note) VALUES (?,?,?,?)",
                   (g.party["id"], tid, g.guest["id"], (b.get("note") or "")[:120]))
        # Suggesting implies a +1.
        db.execute("INSERT OR REPLACE INTO suggestion_votes (party_id, track_id, guest_id, value) VALUES (?,?,?,1)",
                   (g.party["id"], tid, g.guest["id"]))
        db.commit()
    except Exception:
        return jsonify(error="You already requested that one"), 409
    return jsonify(ok=True), 201


@api.post("/parties/<code>/queue/vote")
@with_party
@guest_required
def queue_vote():
    b = _body()
    tid, v = b.get("track_id"), int(b.get("value", 0))
    if v not in (-1, 1) or not tid:
        return jsonify(error="value must be 1 or -1"), 400
    db = get_db()
    existing = db.execute("SELECT value FROM suggestion_votes WHERE party_id=? AND track_id=? AND guest_id=?",
                          (g.party["id"], tid, g.guest["id"])).fetchone()
    if existing and existing["value"] == v:
        db.execute("DELETE FROM suggestion_votes WHERE party_id=? AND track_id=? AND guest_id=?", (g.party["id"], tid, g.guest["id"]))
    else:
        db.execute("INSERT OR REPLACE INTO suggestion_votes (party_id, track_id, guest_id, value) VALUES (?,?,?,?)",
                   (g.party["id"], tid, g.guest["id"], v))
    db.commit()
    return jsonify(ok=True)


@api.post("/parties/<code>/vote")
@with_party
@guest_required
def vote_now_playing():
    v = int(_body().get("value", 0))
    if v not in (-1, 1):
        return jsonify(error="value must be 1 or -1"), 400
    cur = P.current_play(g.party["id"])
    if not cur:
        return jsonify(error="Nothing is playing"), 400
    db = get_db()
    existing = db.execute("SELECT value FROM play_votes WHERE play_id=? AND guest_id=?", (cur["id"], g.guest["id"])).fetchone()
    if existing and existing["value"] == v:
        db.execute("DELETE FROM play_votes WHERE play_id=? AND guest_id=?", (cur["id"], g.guest["id"]))
        my = None
    else:
        db.execute("INSERT OR REPLACE INTO play_votes (play_id, guest_id, value) VALUES (?,?,?)", (cur["id"], g.guest["id"], v))
        my = v
    db.commit()
    up, down = P.play_votes(cur["id"])
    return jsonify(ok=True, up=up, down=down, my_vote=my)


@api.post("/parties/<code>/themes")
@with_party
@guest_required
def propose_theme():
    text = (_body().get("text") or "").strip()[:60]
    if len(text) < 3:
        return jsonify(error="Give the theme a name"), 400
    ai = current_app.extensions.get("ai")
    parsed = ai.interpret_theme(text) if ai else parse_theme(text)
    db = get_db()
    try:
        cur = db.execute("INSERT INTO themes (party_id, guest_id, text, keywords, year_from, year_to, target_energy) VALUES (?,?,?,?,?,?,?)",
                         (g.party["id"], g.guest["id"], text, json.dumps(parsed["keywords"]), parsed["year_from"], parsed["year_to"], parsed["target_energy"]))
        db.execute("INSERT OR REPLACE INTO theme_votes (theme_id, guest_id, value) VALUES (?,?,1)", (cur.lastrowid, g.guest["id"]))
        db.commit()
    except Exception:
        return jsonify(error="Someone already proposed that — upvote it instead"), 409
    return jsonify(ok=True, parsed=parsed), 201


@api.post("/parties/<code>/themes/<int:tid>/vote")
@with_party
@guest_required
def vote_theme(tid):
    v = int(_body().get("value", 0))
    if v not in (-1, 1):
        return jsonify(error="value must be 1 or -1"), 400
    db = get_db()
    if not db.execute("SELECT 1 FROM themes WHERE id=? AND party_id=?", (tid, g.party["id"])).fetchone():
        return jsonify(error="No such theme"), 404
    existing = db.execute("SELECT value FROM theme_votes WHERE theme_id=? AND guest_id=?", (tid, g.guest["id"])).fetchone()
    if existing and existing["value"] == v:
        db.execute("DELETE FROM theme_votes WHERE theme_id=? AND guest_id=?", (tid, g.guest["id"]))
    else:
        db.execute("INSERT OR REPLACE INTO theme_votes (theme_id, guest_id, value) VALUES (?,?,?)", (tid, g.guest["id"], v))
    db.commit()
    return jsonify(ok=True)


# ---------------------------------------------------------------- host controls
@api.post("/parties/<code>/host/skip")
@with_party
@host_required
def host_skip():
    from ..services.spotify import SpotifyError
    try:
        picked = P.advance(g.party, "skipped_by_host")
    except SpotifyError as e:
        return jsonify(error=f"Couldn't find a next song: {e}"), 502
    if not picked:
        return jsonify(error="No song available to play right now — the queue is empty and the fallback search found nothing."), 409
    return jsonify(ok=True, next=picked["track"])


@api.post("/parties/<code>/host/seed-test-tracks")
@with_party
@host_required
def host_seed_test_tracks():
    """Load a curated list of REAL, verified-URI popular songs into the
    party's crowd queue as suggestions from the host, so autopilot/Skip can
    play real Spotify tracks without ever calling /search (which is what
    was hitting the rate limit). Testing/demo convenience -- real guests
    should use normal search once you're past the rate limit."""
    if current_app.config["SPOTIFY_MOCK"] or not P.spotify_live(g.party):
        return jsonify(error="Connect Spotify for this party first — seeding real tracks only makes sense in live mode."), 400
    tracks = seed_real_tracks(g.party, P.upsert_track, get_db())
    added = 0
    for t in tracks:
        try:
            get_db().execute("INSERT INTO suggestions (party_id, track_id, guest_id, note) VALUES (?,?,?,?)",
                             (g.party["id"], t["id"], g.guest["id"], "test track"))
            get_db().execute("INSERT OR REPLACE INTO suggestion_votes (party_id, track_id, guest_id, value) VALUES (?,?,?,1)",
                             (g.party["id"], t["id"], g.guest["id"]))
            added += 1
        except Exception:
            pass  # already suggested; fine, skip
    get_db().commit()
    return jsonify(ok=True, added=added, total=len(tracks))


@api.post("/parties/<code>/host/play")
@with_party
@host_required
def host_play():
    """Force a specific candidate to play now."""
    tid = _body().get("track_id")
    t = P.get_track(tid)
    if not t:
        return jsonify(error="Unknown track"), 404
    cur = P.current_play(g.party["id"])
    if cur:
        P.end_play(cur["id"], "replaced")
    ctx = P.party_context(g.party)
    from ..services import dj
    s, comp = dj.score_candidate(t, dict(ctx, n_suggesters=0, net_votes=0, first_suggested_min_ago=0, played_recently=False))
    P.start_play(g.party, {"track": t, "score": s, "components": comp, "explanation": "host pick"}, chosen_by="host")
    return jsonify(ok=True)


@api.post("/parties/<code>/host/settings")
@with_party
@host_required
def host_settings():
    b = _body()
    db = get_db()
    if "auto_dj" in b:
        db.execute("UPDATE parties SET auto_dj=? WHERE id=?", (int(bool(b["auto_dj"])), g.party["id"]))
    if "pinned_theme_id" in b:
        db.execute("UPDATE parties SET pinned_theme_id=? WHERE id=?", (b["pinned_theme_id"], g.party["id"]))
    if "block_explicit" in b:
        s = json.loads(g.party["settings"] or "{}"); s["block_explicit"] = bool(b["block_explicit"])
        db.execute("UPDATE parties SET settings=? WHERE id=?", (json.dumps(s), g.party["id"]))
    db.commit()
    return jsonify(ok=True)


@api.post("/parties/<code>/host/remove")
@with_party
@host_required
def host_remove():
    tid = _body().get("track_id")
    db = get_db()
    db.execute("DELETE FROM suggestions WHERE party_id=? AND track_id=?", (g.party["id"], tid))
    db.execute("DELETE FROM suggestion_votes WHERE party_id=? AND track_id=?", (g.party["id"], tid))
    db.commit()
    return jsonify(ok=True)


# ---------------------------------------------------------------- Spotify OAuth (host)
@api.get("/parties/<code>/spotify/login")
@with_party
@host_required
def spotify_login():
    sp = current_app.extensions.get("spotify")
    if not sp or current_app.config["SPOTIFY_MOCK"]:
        return jsonify(error="Set SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET to enable Spotify"), 400
    return redirect(sp.auth_url(state=g.party["code"]))


@api.get("/parties/<code>/spotify/devices")
@with_party
@host_required
def spotify_devices():
    if not P.spotify_live(g.party):
        return jsonify(devices=[])
    sp = current_app.extensions["spotify"]
    toks, ch = sp.ensure_fresh(P.tokens_for(g.party))
    if ch:
        P.save_tokens(g.party["id"], toks)
    return jsonify(devices=sp.devices(toks))
