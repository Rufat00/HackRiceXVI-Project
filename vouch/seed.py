"""Populate a fresh demo database.

    python seed.py            # creates users + listings against a running server? No — direct.

Runs in-process (no server needed). Creates:
  admin@vouch.demo / demo123   — moderator (sees the Disputes tab)
  sam@uh.edu       / demo123   — verified seller with 4 live listings
  bea@uh.edu       / demo123   — verified buyer
  nate@uh.edu      / demo123   — registered but NOT verified (show the gate)

Listing photos are generated placeholders that show the proof word, since a
real camera isn't available at seed time. Anything you list live in the demo
will use the real in-app camera.
"""
import base64
import io
import os
import sys

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import create_app  # noqa: E402

PW = "demo123"


def photo(word, label, color):
    img = Image.new("RGB", (800, 600), color)
    d = ImageDraw.Draw(img)
    try:
        big = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 64)
        small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
    except OSError:
        big = small = ImageFont.load_default()
    d.rounded_rectangle((60, 60, 740, 420), 18, fill=(245, 243, 236), outline=(200, 200, 190), width=3)
    d.text((90, 100), label, fill=(40, 40, 40), font=small)
    d.text((90, 150), "(demo placeholder photo)", fill=(120, 120, 120), font=small)
    # the "sticky note" with the proof word
    d.rounded_rectangle((420, 300, 740, 560), 10, fill=(255, 235, 120), outline=(210, 180, 60), width=3)
    d.text((445, 380), word, fill=(30, 30, 30), font=big)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=80)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


LISTINGS = [
    ("Stewart Calculus 8e, hardcover", "item", 40, "Light highlighting in ch. 11 (power series). Pickup at Fondren.", (200, 215, 205)),
    ("Two Rodeo tickets, Sec 112 Row F", "ticket", 85, "March 7 show. Will transfer via the venue app at handoff.", (215, 205, 190)),
    ("Summer sublet: 1BR near Rice Village", "sublet", 450, "Jun 1–Aug 15. Furnished, utilities included. Deposit only; rent paid to landlord.", (205, 210, 220), "2401 Dunstan Rd #3"),
    ("TI-84 Plus CE, barely used", "item", 60, "Comes with charger. Bought for Calc II, don't need it anymore.", (210, 200, 215)),
]


def main():
    app = create_app()
    c = app.test_client()

    def reg(email, name):
        r = c.post("/api/auth/register", json={"email": email, "password": PW, "display_name": name})
        if r.status_code == 409:
            c.post("/api/auth/login", json={"email": email, "password": PW})
        return r

    def verify(first, last, dob="2003-06-01"):
        return c.post("/api/verify/complete", json={"outcome": "pass", "first_name": first, "last_name": last, "birthdate": dob})

    reg("admin@vouch.demo", "Vouch Moderator"); verify("Vouch", "Moderator")
    c.post("/api/auth/logout")

    reg("sam@uh.edu", "Sam Okafor"); verify("Samuel", "Okafor")
    for row in LISTINGS:
        title, cat, price, desc, color = row[:5]
        addr = row[5] if len(row) > 5 else None
        r = c.post("/api/listings", json={"title": title, "category": cat, "price": price, "description": desc, "address": addr})
        if r.status_code != 201:
            print("skip", title, r.json); continue
        word = r.json["proof_word"]
        c.post(f"/api/listings/{r.json['listing_id']}/photo", json={"photo_data": photo(word, title, color)})
        print("listed", title, "| proof word:", word)
    c.post("/api/auth/logout")

    reg("bea@uh.edu", "Bea Tran"); verify("Beatrice", "Tran"); c.post("/api/auth/logout")
    reg("nate@uh.edu", "Nate Park"); c.post("/api/auth/logout")

    print("\nAccounts (password: demo123)")
    print("  admin@vouch.demo  moderator")
    print("  sam@uh.edu        verified seller")
    print("  bea@uh.edu        verified buyer")
    print("  nate@uh.edu       unverified (demo the gate)")


if __name__ == "__main__":
    main()
