"""
Verified campus marketplace — Flask API + static frontend.

Run:  python app.py      (mock mode, no keys needed)
Real: see .env.example and README.md
"""
import os
import time
import base64
import functools
from flask import Flask, request, jsonify, session, send_from_directory

import store
import nessie
import persona
from store import conn, row, new_id, log, transition

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-me")
PHOTO_DIR = os.path.join(app.static_folder, "photos")
os.makedirs(PHOTO_DIR, exist_ok=True)
store.init()

STARTING_BALANCE = 500.0  # mock money each verified user gets in Nessie


# ---------------------------------------------------------------- helpers
def current_user(c):
    uid = session.get("uid")
    if not uid:
        return None
    return row(c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())


def public_user(u):
    if not u:
        return None
    return {k: u[k] for k in ("id", "name", "email", "verified", "verified_name",
                              "is_admin", "banned")}


def err(msg, code=400):
    return jsonify({"error": msg}), code


def require_user(verified=False, admin=False):
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*a, **kw):
            with conn() as c:
                u = current_user(c)
            if not u:
                return err("Sign in first", 401)
            if u["banned"]:
                return err("This account has been banned", 403)
            if verified and not u["verified"]:
                return err("Verify your identity before you can buy or sell", 403)
            if admin and not u["is_admin"]:
                return err("Admin only", 403)
            return fn(u, *a, **kw)
        return wrapper
    return deco


def settle_overdue():
    """Auto-release / auto-refund deals past their windows. Called on reads."""
    with conn() as c:
        release, refund = store.overdue_deals(c)
        for d in release:
            seller = row(c.execute("SELECT * FROM users WHERE id=?", (d["seller_id"],)).fetchone())
            nessie.transfer(d["escrow_account"], seller["nessie_account"], d["amount"],
                            f"Auto-release deal {d['id']}")
            transition(c, d, "released", "system", "confirmation window expired")
        for d in refund:
            buyer = row(c.execute("SELECT * FROM users WHERE id=?", (d["buyer_id"],)).fetchone())
            nessie.transfer(d["escrow_account"], buyer["nessie_account"], d["amount"],
                            f"Auto-refund deal {d['id']}")
            transition(c, d, "refunded", "system", "seller never handed off")
            c.execute("UPDATE listings SET status='open' WHERE id=?", (d["listing_id"],))


def deal_view(c, d, viewer_id):
    d = dict(d)
    d["listing"] = row(c.execute("SELECT id,title,price,photo FROM listings WHERE id=?",
                                 (d["listing_id"],)).fetchone())
    d["buyer"] = public_user(row(c.execute("SELECT * FROM users WHERE id=?", (d["buyer_id"],)).fetchone()))
    d["seller"] = public_user(row(c.execute("SELECT * FROM users WHERE id=?", (d["seller_id"],)).fetchone()))
    d["events"] = [row(e) for e in c.execute(
        "SELECT * FROM events WHERE deal_id=? ORDER BY at", (d["id"],))]
    try:
        d["escrow_balance"] = nessie.get_account(d["escrow_account"])["balance"]
    except Exception:
        d["escrow_balance"] = None
    # The code is only ever shown to the buyer.
    if viewer_id != d["buyer_id"]:
        d["handoff_code"] = None
    d["reviewed"] = c.execute("SELECT 1 FROM reviews WHERE deal_id=?", (d["id"],)).fetchone() is not None
    d["role"] = "buyer" if viewer_id == d["buyer_id"] else "seller" if viewer_id == d["seller_id"] else "admin"
    d["confirm_deadline"] = (d["handed_off_at"] + store.CONFIRM_WINDOW_SECONDS) if d["handed_off_at"] else None
    return d


# ------------------------------------------------------------------ pages
@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/config")
def config():
    return jsonify({"nessie_mode": nessie.mode(), "persona": persona.public_config(),
                    "confirm_window_seconds": store.CONFIRM_WINDOW_SECONDS})


# ------------------------------------------------------------------- auth
@app.post("/api/signup")
def signup():
    data = request.get_json(force=True)
    name, email = (data.get("name") or "").strip(), (data.get("email") or "").strip().lower()
    if not name or not email:
        return err("Name and email are required")
    with conn() as c:
        existing = row(c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone())
        if existing:
            session["uid"] = existing["id"]
            return jsonify({"user": public_user(existing), "returning": True})
        first_admin = c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        uid = new_id("usr")
        c.execute("INSERT INTO users (id,name,email,is_admin,created) VALUES (?,?,?,?,?)",
                  (uid, name, email, 1 if first_admin else 0, time.time()))
        session["uid"] = uid
        u = row(c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())
    return jsonify({"user": public_user(u), "returning": False})


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


@app.get("/api/me")
def me():
    settle_overdue()
    with conn() as c:
        u = current_user(c)
        out = {"user": public_user(u)}
        if u and u["nessie_account"]:
            try:
                out["balance"] = nessie.get_account(u["nessie_account"])["balance"]
            except Exception:
                out["balance"] = None
        if u:
            revs = c.execute("SELECT rating FROM reviews WHERE reviewee_id=?", (u["id"],)).fetchall()
            out["reputation"] = {"count": len(revs),
                                 "avg": round(sum(r["rating"] for r in revs) / len(revs), 1) if revs else None}
    return jsonify(out)


@app.post("/api/verify")
@require_user()
def verify(u):
    """Browser posts Persona inquiry result; we confirm server-side."""
    data = request.get_json(force=True)
    inquiry_id = data.get("inquiry_id") or "mock-inquiry"
    try:
        result = persona.check_inquiry(inquiry_id, data.get("status"))
    except persona.PersonaError as e:
        return err(str(e), 502)
    if not result["verified"]:
        return jsonify({"verified": False, "status": result["status"],
                        "message": "We couldn't verify you. You can browse, but not buy or sell."})
    with conn() as c:
        # One human, one account: the same inquiry (identity) can't verify twice.
        dupe = c.execute("SELECT id FROM users WHERE inquiry_id=? AND id!=?",
                         (inquiry_id, u["id"])).fetchone()
        if dupe and not persona.MOCK:
            return err("This identity is already attached to another account", 409)
        first, _, last = (result["name"] or u["name"]).partition(" ")
        cust = nessie.create_customer(first, last)
        acct = nessie.create_account(cust, f"{u['name']} wallet", STARTING_BALANCE)
        c.execute("""UPDATE users SET verified=1, inquiry_id=?, verified_name=?,
                     nessie_customer=?, nessie_account=? WHERE id=?""",
                  (inquiry_id, result["name"], cust, acct["_id"], u["id"]))
    return jsonify({"verified": True, "status": result["status"], "wallet": acct["_id"],
                    "balance": acct["balance"]})


# --------------------------------------------------------------- listings
@app.get("/api/listings")
def listings():
    settle_overdue()
    with conn() as c:
        rows = c.execute("""SELECT l.*, u.name AS seller_name, u.verified_name AS seller_verified_name
                            FROM listings l JOIN users u ON u.id=l.seller_id
                            WHERE l.status='open' ORDER BY l.created DESC""").fetchall()
        out = []
        for l in rows:
            l = dict(l)
            revs = c.execute("SELECT rating FROM reviews WHERE reviewee_id=?", (l["seller_id"],)).fetchall()
            l["seller_rep"] = {"count": len(revs),
                               "avg": round(sum(r["rating"] for r in revs) / len(revs), 1) if revs else None}
            out.append(l)
    return jsonify(out)


@app.get("/api/proof-word")
@require_user(verified=True)
def get_proof_word(u):
    """Seller gets a random word to write on paper before taking the listing photo."""
    word = store.proof_word()
    session["proof_word"] = word
    return jsonify({"proof_word": word})


@app.post("/api/listings")
@require_user(verified=True)
def create_listing(u):
    data = request.get_json(force=True)
    title = (data.get("title") or "").strip()
    try:
        price = float(data.get("price"))
    except (TypeError, ValueError):
        return err("Price must be a number")
    if not title or price <= 0:
        return err("Title and a positive price are required")
    photo_b64 = data.get("photo")
    if not photo_b64:
        return err("Take a photo of the item in the app — uploads aren't allowed")
    word = session.get("proof_word")
    if not word:
        return err("Get a proof word before taking the photo")
    lid = new_id("lst")
    try:
        raw = base64.b64decode(photo_b64.split(",")[-1])
    except Exception:
        return err("Bad photo data")
    path = os.path.join(PHOTO_DIR, f"{lid}.jpg")
    with open(path, "wb") as f:
        f.write(raw)
    with conn() as c:
        c.execute("""INSERT INTO listings (id,seller_id,title,category,price,description,photo,proof_word,created)
                     VALUES (?,?,?,?,?,?,?,?,?)""",
                  (lid, u["id"], title, data.get("category", "other"), price,
                   data.get("description", ""), f"/static/photos/{lid}.jpg", word, time.time()))
    session.pop("proof_word", None)
    return jsonify({"id": lid})


# ------------------------------------------------------------------ deals
@app.post("/api/deals")
@require_user(verified=True)
def open_deal(u):
    """Buyer funds escrow. Money leaves the buyer's wallet immediately."""
    data = request.get_json(force=True)
    with conn() as c:
        l = row(c.execute("SELECT * FROM listings WHERE id=? AND status='open'",
                          (data.get("listing_id"),)).fetchone())
        if not l:
            return err("Listing not available", 404)
        if l["seller_id"] == u["id"]:
            return err("You can't buy your own listing")
        did = new_id("deal")
        escrow = nessie.create_account(u["nessie_customer"], f"escrow {did}", 0)
        try:
            nessie.transfer(u["nessie_account"], escrow["_id"], l["price"],
                            f"Escrow for {l['title']}")
        except nessie.NessieError as e:
            return err(f"Payment failed: {e}", 402)
        c.execute("""INSERT INTO deals (id,listing_id,buyer_id,seller_id,amount,escrow_account,state,handoff_code,funded_at)
                     VALUES (?,?,?,?,?,?,?,?,?)""",
                  (did, l["id"], u["id"], l["seller_id"], l["price"], escrow["_id"],
                   "funded", store.make_handoff_code(), time.time()))
        c.execute("UPDATE listings SET status='pending' WHERE id=?", (l["id"],))
        log(c, did, u["id"], "funded", f"${l['price']:.2f} moved to escrow {escrow['_id']}")
        d = row(c.execute("SELECT * FROM deals WHERE id=?", (did,)).fetchone())
        return jsonify(deal_view(c, d, u["id"]))


@app.get("/api/deals")
@require_user()
def my_deals(u):
    settle_overdue()
    with conn() as c:
        if u["is_admin"] and request.args.get("all"):
            rows = c.execute("SELECT * FROM deals ORDER BY funded_at DESC").fetchall()
        else:
            rows = c.execute("SELECT * FROM deals WHERE buyer_id=? OR seller_id=? ORDER BY funded_at DESC",
                             (u["id"], u["id"])).fetchall()
        return jsonify([deal_view(c, d, u["id"]) for d in rows])


@app.post("/api/deals/<did>/handoff")
@require_user(verified=True)
def handoff(u, did):
    """Seller types the buyer's code in person. Proves both parties met."""
    code = (request.get_json(force=True).get("code") or "").strip()
    with conn() as c:
        d = row(c.execute("SELECT * FROM deals WHERE id=?", (did,)).fetchone())
        if not d or d["seller_id"] != u["id"]:
            return err("Not your deal", 403)
        if code != d["handoff_code"]:
            log(c, did, u["id"], "bad_code", "wrong handoff code entered")
            return err("That code doesn't match. Ask the buyer to show you theirs.")
        try:
            d = transition(c, d, "handed_off", u["id"], "code verified in person")
        except ValueError as e:
            return err(str(e))
        return jsonify(deal_view(c, d, u["id"]))


@app.post("/api/deals/<did>/confirm")
@require_user(verified=True)
def confirm(u, did):
    """Buyer confirms item is as described. Escrow -> seller."""
    with conn() as c:
        d = row(c.execute("SELECT * FROM deals WHERE id=?", (did,)).fetchone())
        if not d or d["buyer_id"] != u["id"]:
            return err("Not your deal", 403)
        seller = row(c.execute("SELECT * FROM users WHERE id=?", (d["seller_id"],)).fetchone())
        try:
            d = transition(c, d, "released", u["id"], "buyer confirmed")
        except ValueError as e:
            return err(str(e))
        nessie.transfer(d["escrow_account"], seller["nessie_account"], d["amount"],
                        f"Release deal {did}")
        c.execute("UPDATE listings SET status='sold' WHERE id=?", (d["listing_id"],))
        return jsonify(deal_view(c, d, u["id"]))


@app.post("/api/deals/<did>/dispute")
@require_user(verified=True)
def dispute(u, did):
    reason = (request.get_json(force=True).get("reason") or "").strip() or "no reason given"
    with conn() as c:
        d = row(c.execute("SELECT * FROM deals WHERE id=?", (did,)).fetchone())
        if not d or u["id"] not in (d["buyer_id"], d["seller_id"]):
            return err("Not your deal", 403)
        try:
            d = transition(c, d, "disputed", u["id"], reason)
        except ValueError as e:
            return err(str(e))
        c.execute("UPDATE deals SET dispute_reason=?, dispute_by=? WHERE id=?", (reason, u["id"], did))
        return jsonify(deal_view(c, d, u["id"]))


@app.post("/api/deals/<did>/review")
@require_user(verified=True)
def review(u, did):
    """Reputation only comes from completed escrow deals between two verified people."""
    data = request.get_json(force=True)
    with conn() as c:
        d = row(c.execute("SELECT * FROM deals WHERE id=?", (did,)).fetchone())
        if not d or d["state"] != "released" or d["buyer_id"] != u["id"]:
            return err("Only the buyer of a completed deal can review", 403)
        rating = int(data.get("rating", 0))
        if not 1 <= rating <= 5:
            return err("Rating must be 1-5")
        try:
            c.execute("INSERT INTO reviews (id,deal_id,reviewer_id,reviewee_id,rating,text,created) VALUES (?,?,?,?,?,?,?)",
                      (new_id("rev"), did, u["id"], d["seller_id"], rating, data.get("text", ""), time.time()))
        except Exception:
            return err("Already reviewed", 409)
    return jsonify({"ok": True})


# ------------------------------------------------------------------ admin
@app.post("/api/admin/deals/<did>/resolve")
@require_user(admin=True)
def resolve(u, did):
    """Admin decides a dispute: 'seller' releases, 'buyer' refunds. Optionally bans."""
    data = request.get_json(force=True)
    winner = data.get("winner")
    with conn() as c:
        d = row(c.execute("SELECT * FROM deals WHERE id=?", (did,)).fetchone())
        if not d or d["state"] != "disputed":
            return err("Deal is not in dispute")
        if winner == "seller":
            to, target = "released", d["seller_id"]
        elif winner == "buyer":
            to, target = "refunded", d["buyer_id"]
        else:
            return err("winner must be 'buyer' or 'seller'")
        tgt = row(c.execute("SELECT * FROM users WHERE id=?", (target,)).fetchone())
        d = transition(c, d, to, u["id"], f"admin ruled for {winner}: {data.get('note','')}")
        nessie.transfer(d["escrow_account"], tgt["nessie_account"], d["amount"], f"Resolve deal {did}")
        c.execute("UPDATE listings SET status=? WHERE id=?",
                  ("sold" if to == "released" else "open", d["listing_id"]))
        if data.get("ban"):
            loser = d["buyer_id"] if winner == "seller" else d["seller_id"]
            c.execute("UPDATE users SET banned=1 WHERE id=?", (loser,))
            log(c, did, u["id"], "ban", f"user {loser} banned — identity is verified, so this sticks")
        return jsonify(deal_view(c, d, u["id"]))


@app.post("/api/dev/fast-forward")
@require_user(admin=True)
def fast_forward(u):
    """Demo helper: age all open deals so auto-release / auto-refund fire."""
    with conn() as c:
        c.execute("UPDATE deals SET handed_off_at = handed_off_at - ? WHERE state='handed_off'",
                  (store.CONFIRM_WINDOW_SECONDS + 1,))
        c.execute("UPDATE deals SET funded_at = funded_at - ? WHERE state='funded'",
                  (store.HANDOFF_WINDOW_SECONDS + 1,))
    settle_overdue()
    return jsonify({"ok": True})


if __name__ == "__main__":
    print(f"Nessie: {nessie.mode()} | Persona: {'mock' if persona.MOCK else 'live'}")
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
