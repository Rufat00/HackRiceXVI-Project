"""Run: python -m pytest -q   (or: python tests/test_flow.py)"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import create_app  # noqa: E402

FAKE_PHOTO = "data:image/jpeg;base64," + ("A" * 3000)


def make_app(**cfg):
    d = tempfile.mkdtemp()
    base = dict(TESTING=True, DATABASE_PATH=os.path.join(d, "t.db"), NESSIE_MOCK=True, PERSONA_MOCK=True,
                SECRET_KEY="t", ADMIN_EMAILS=[])
    base.update(cfg)
    return create_app(base)


def register(c, email, name, verify=True, first=None, last=None, dob="2003-01-01", outcome="pass"):
    r = c.post("/api/auth/register", json={"email": email, "password": "secret1", "display_name": name})
    assert r.status_code == 201, r.json
    if verify:
        r = c.post("/api/verify/complete", json={"outcome": outcome, "first_name": first or name,
                                                 "last_name": last or "Test", "birthdate": dob})
        return r
    return r


def list_item(c, price=40.0, category="item", address=None):
    r = c.post("/api/listings", json={"title": "Calc textbook", "category": category, "price": price,
                                      "description": "Stewart 8e", "address": address})
    assert r.status_code == 201, r.json
    lid = r.json["listing_id"]
    assert r.json["proof_word"]
    r = c.post(f"/api/listings/{lid}/photo", json={"photo_data": FAKE_PHOTO})
    assert r.status_code == 200, r.json
    return lid


def test_unverified_user_cannot_list_or_buy():
    app = make_app()
    c = app.test_client()
    register(c, "a@uh.edu", "Alice", verify=False)
    r = c.post("/api/listings", json={"title": "x", "category": "item", "price": 5})
    assert r.status_code == 403 and r.json["code"] == "unverified"


def test_failed_verification_is_rejected():
    app = make_app()
    c = app.test_client()
    r = register(c, "bot@uh.edu", "Botty", outcome="fail")
    assert r.status_code == 403
    assert not c.get("/api/me").json["user"]["verified"]


def test_underage_rejected():
    app = make_app()
    c = app.test_client()
    r = register(c, "kid@uh.edu", "Kid", dob="2012-01-01")
    assert r.status_code == 403 and "18+" in r.json["error"]


def test_one_identity_one_account():
    app = make_app()
    c1, c2 = app.test_client(), app.test_client()
    assert register(c1, "a@uh.edu", "Alice", first="Jane", last="Doe", dob="2000-05-05").status_code == 200
    r = register(c2, "a2@uh.edu", "Alice Again", first="Jane", last="Doe", dob="2000-05-05")
    assert r.status_code == 403 and "already verified" in r.json["error"]


def test_happy_path_money_flow():
    app = make_app()
    seller, buyer = app.test_client(), app.test_client()
    register(seller, "s@uh.edu", "Sam", first="Sam", last="Seller")
    register(buyer, "b@uh.edu", "Bea", first="Bea", last="Buyer")
    lid = list_item(seller, price=40)

    # listing is public
    assert any(l["id"] == lid for l in buyer.get("/api/listings").json["listings"])

    r = buyer.post(f"/api/listings/{lid}/buy")
    assert r.status_code == 201
    tid = r.json["transaction"]["id"]
    assert r.json["transaction"]["state"] == "created"

    # seller cannot fund; only buyer
    assert seller.post(f"/api/transactions/{tid}/fund").status_code == 403

    r = buyer.post(f"/api/transactions/{tid}/fund")
    assert r.status_code == 200, r.json
    code = r.json["handoff_code"]
    t = r.json["transaction"]
    assert t["state"] == "funded"
    assert t["escrow_account"]["balance_cents"] == 4000
    assert buyer.get("/api/me").json["user"]["wallet"]["balance_cents"] == 50000 - 4000
    # code never stored in plaintext
    assert "handoff_code_hash" not in t

    # wrong code rejected
    r = seller.post(f"/api/transactions/{tid}/handoff", json={"code": "000000" if code != "000000" else "111111"})
    assert r.status_code == 400
    # buyer can't enter it for the seller
    assert buyer.post(f"/api/transactions/{tid}/handoff", json={"code": code}).status_code == 403
    r = seller.post(f"/api/transactions/{tid}/handoff", json={"code": code})
    assert r.status_code == 200 and r.json["transaction"]["state"] == "handed_off"

    # seller cannot confirm on the buyer's behalf
    assert seller.post(f"/api/transactions/{tid}/confirm").status_code == 403
    r = buyer.post(f"/api/transactions/{tid}/confirm")
    assert r.status_code == 200 and r.json["transaction"]["state"] == "released"
    assert r.json["transaction"]["escrow_account"]["balance_cents"] == 0
    assert seller.get("/api/me").json["user"]["wallet"]["balance_cents"] == 50000 + 4000
    assert buyer.get(f"/api/listings/{lid}").json["listing"]["status"] == "sold"

    # rating only after release, once
    assert buyer.post(f"/api/transactions/{tid}/rate", json={"stars": 5, "comment": "smooth"}).status_code == 200
    assert buyer.post(f"/api/transactions/{tid}/rate", json={"stars": 1}).status_code == 400
    rep = buyer.get(f"/api/users/{t['seller_id']}").json["user"]["reputation"]
    assert rep == {"ratings": 1, "avg_stars": 5.0, "completed_transactions": 1}


def test_dispute_and_admin_refund():
    app = make_app()
    admin, seller, buyer = app.test_client(), app.test_client(), app.test_client()
    register(admin, "admin@uh.edu", "Admin", first="Ad", last="Min")  # first user = admin
    register(seller, "s@uh.edu", "Sam", first="Sam", last="Seller")
    register(buyer, "b@uh.edu", "Bea", first="Bea", last="Buyer")
    lid = list_item(seller, price=120)
    tid = buyer.post(f"/api/listings/{lid}/buy").json["transaction"]["id"]
    code = buyer.post(f"/api/transactions/{tid}/fund").json["handoff_code"]
    seller.post(f"/api/transactions/{tid}/handoff", json={"code": code})

    # too-short reason rejected
    assert buyer.post(f"/api/transactions/{tid}/dispute", json={"reason": "bad"}).status_code == 400
    r = buyer.post(f"/api/transactions/{tid}/dispute", json={"reason": "Pages are torn out and it's the 6th edition"})
    assert r.status_code == 200 and r.json["transaction"]["state"] == "disputed"
    # frozen: buyer can't confirm, seller can't get paid
    assert buyer.post(f"/api/transactions/{tid}/confirm").status_code == 400

    assert seller.get("/api/admin/disputes").status_code == 403
    d = admin.get("/api/admin/disputes").json["disputes"]
    assert len(d) == 1 and d[0]["id"] == tid
    r = admin.post(f"/api/admin/transactions/{tid}/resolve", json={"outcome": "refund", "note": "photo shows 6e"})
    assert r.status_code == 200 and r.json["transaction"]["state"] == "refunded"
    assert buyer.get("/api/me").json["user"]["wallet"]["balance_cents"] == 50000
    assert buyer.get(f"/api/listings/{lid}").json["listing"]["status"] == "active"


def test_timers_auto_refund_and_auto_release():
    app = make_app(HANDOFF_DEADLINE_SECONDS=1, CONFIRM_WINDOW_SECONDS=1)
    seller, buyer = app.test_client(), app.test_client()
    register(seller, "s@uh.edu", "Sam", first="Sam", last="Seller")
    register(buyer, "b@uh.edu", "Bea", first="Bea", last="Buyer")

    # seller ghosts -> auto refund
    lid = list_item(seller, price=10)
    tid = buyer.post(f"/api/listings/{lid}/buy").json["transaction"]["id"]
    buyer.post(f"/api/transactions/{tid}/fund")
    time.sleep(1.2)
    t = buyer.get(f"/api/transactions/{tid}").json["transaction"]
    assert t["state"] == "refunded" and "missed handoff" in t["resolution"]

    # buyer goes silent after handoff -> auto release
    lid2 = list_item(seller, price=10)
    tid2 = buyer.post(f"/api/listings/{lid2}/buy").json["transaction"]["id"]
    code = buyer.post(f"/api/transactions/{tid2}/fund").json["handoff_code"]
    seller.post(f"/api/transactions/{tid2}/handoff", json={"code": code})
    time.sleep(1.2)
    t = seller.get(f"/api/transactions/{tid2}").json["transaction"]
    assert t["state"] == "released" and "window elapsed" in t["resolution"]


def test_chat_flags_off_platform_pressure():
    app = make_app()
    seller, buyer = app.test_client(), app.test_client()
    register(seller, "s@uh.edu", "Sam", first="Sam", last="Seller")
    register(buyer, "b@uh.edu", "Bea", first="Bea", last="Buyer")
    lid = list_item(seller)
    tid = buyer.post(f"/api/listings/{lid}/buy").json["transaction"]["id"]
    r = seller.post(f"/api/transactions/{tid}/messages", json={"body": "just venmo me @sam-sells and I'll knock $10 off"})
    assert r.status_code == 201 and r.flagged if False else r.json["flagged"] is True
    r = seller.post(f"/api/transactions/{tid}/messages", json={"body": "Meet at Fondren at 3?"})
    assert r.json["flagged"] is False
    msgs = buyer.get(f"/api/transactions/{tid}/messages").json["messages"]
    assert msgs[0]["flagged"] == 1 and "payment app" in msgs[0]["flag_reason"]


def test_sublet_needs_address_and_respects_cap():
    app = make_app()
    c = app.test_client()
    register(c, "s@uh.edu", "Sam", first="Sam", last="Seller")
    r = c.post("/api/listings", json={"title": "Room", "category": "sublet", "price": 300})
    assert r.status_code == 400 and "address" in r.json["error"]
    r = c.post("/api/listings", json={"title": "Room", "category": "sublet", "price": 900, "address": "1 Main"})
    assert r.status_code == 400 and "verified lease" in r.json["error"]
    r = c.post("/api/listings", json={"title": "Room", "category": "sublet", "price": 300, "address": "1 Main"})
    assert r.status_code == 201


def test_photo_must_be_in_app_capture():
    app = make_app()
    c = app.test_client()
    register(c, "s@uh.edu", "Sam", first="Sam", last="Seller")
    lid = c.post("/api/listings", json={"title": "x", "category": "item", "price": 5}).json["listing_id"]
    assert c.post(f"/api/listings/{lid}/photo", json={"photo_data": "https://imgur.com/stolen.jpg"}).status_code == 400
    # draft listings aren't public
    assert not any(l["id"] == lid for l in c.get("/api/listings").json["listings"])


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main(["-q", __file__]))
