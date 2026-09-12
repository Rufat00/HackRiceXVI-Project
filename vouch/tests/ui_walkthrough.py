"""Drives the full demo in a headless browser and saves screenshots to /home/claude/shots.
Run with the server up: python tests/ui_walkthrough.py"""
import os
import sys
from playwright.sync_api import sync_playwright

BASE = os.environ.get("BASE", "http://localhost:5000")
OUT = "/home/claude/shots"
os.makedirs(OUT, exist_ok=True)
# 1x1 white JPEG padded to pass the "real capture" size check
FAKE_PHOTO = "data:image/jpeg;base64," + ("/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAAMCAgICAgMCAgIDAwMDBAYEBAQEBAgGBgUGCQgKCgkICQkKDA8MCgsOCwkJDRENDg8QEBEQCgwSExIQEw8QEBD/2wBDAQMDAwQDBAgEBAgQCwkLEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBD/wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAn/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFAEBAAAAAAAAAAAAAAAAAAAAAP/EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAMAwEAAhEDEQA/AL+AAf/Z" * 20)


def shot(page, name):
    page.screenshot(path=f"{OUT}/{name}.png", full_page=True)
    print("shot", name)


def register(page, email, name):
    page.goto(BASE + "/#/")
    page.click("#signup")
    page.fill("input[name=display_name]", name)
    page.fill("input[name=email]", email)
    page.fill("input[name=password]", "secret1")
    page.click("#authf button.btn")
    page.wait_for_url("**/#/verify")
    page.wait_for_selector("#mockf")


def verify(page, first, last, outcome="pass", dob="2004-03-15"):
    page.fill("input[name=first_name]", first)
    page.fill("input[name=last_name]", last)
    page.fill("input[name=birthdate]", dob)
    page.select_option("select[name=outcome]", outcome)
    page.click("#mockf button.btn")


with sync_playwright() as p:
    browser = p.chromium.launch(executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
    seller = browser.new_context(viewport={"width": 1280, "height": 900})
    buyer = browser.new_context(viewport={"width": 1280, "height": 900})
    bot = browser.new_context(viewport={"width": 1280, "height": 900})
    sp, bp, xp = seller.new_page(), buyer.new_page(), bot.new_page()
    for pg in (sp, bp, xp):
        pg.on("pageerror", lambda e: print("PAGE ERROR:", e))
        pg.on("console", lambda m: print("console:", m.type, m.text) if m.type == "error" else None)

    # landing
    sp.goto(BASE); sp.wait_for_selector(".hero"); shot(sp, "01_landing")

    # scammer bounced
    register(xp, "bot@example.com", "Totally Real")
    shot(xp, "02_verify_page")
    verify(xp, "Fake", "Person", outcome="fail")
    xp.wait_for_selector(".toast.err"); shot(xp, "03_verify_failed")

    # seller (first user = admin) — actually bot registered first; make seller admin-less, fine.
    register(sp, "sam@uh.edu", "Sam Okafor")
    verify(sp, "Samuel", "Okafor")
    sp.wait_for_url("**/#/")
    # list an item via API using the page's cookies (camera can't run headless), then view it
    r = sp.request.post(BASE + "/api/listings", data={"title": "Stewart Calculus 8e", "category": "item", "price": 40,
                                                       "description": "Hardcover, light highlighting in ch. 11 (power series). Pickup at Fondren."})
    lid = r.json()["listing_id"]
    sp.goto(BASE + "/#/sell"); sp.wait_for_selector("#sellf"); shot(sp, "04_sell_form")
    sp.request.post(BASE + f"/api/listings/{lid}/photo", data={"photo_data": FAKE_PHOTO})
    sp.goto(BASE + f"/#/listing/{lid}"); sp.wait_for_selector("#remove"); shot(sp, "05_listing_seller_view")

    # buyer
    register(bp, "bea@uh.edu", "Bea Tran")
    verify(bp, "Beatrice", "Tran")
    bp.wait_for_url("**/#/")
    bp.wait_for_selector(".grid .card"); shot(bp, "06_browse")
    bp.click(".grid .card"); bp.wait_for_selector("#buy"); shot(bp, "07_listing_buyer_view")
    bp.click("#buy"); bp.wait_for_selector("#fund"); shot(bp, "08_deal_created")
    bp.click("#fund"); bp.wait_for_selector(".handoff-code")
    code = bp.inner_text(".handoff-code").strip(); print("code", code)
    shot(bp, "09_deal_funded_buyer")

    # chat with a flagged message from seller
    sp.goto(BASE + "/#/deals"); sp.wait_for_selector(".deal"); shot(sp, "10_deals_list_seller")
    sp.click(".deal"); sp.wait_for_selector("#hf")
    sp.fill("#chatf input", "honestly just venmo me @sam-okafor and I'll knock $5 off, skip the app")
    sp.click("#chatf button"); sp.wait_for_selector(".msg.flagged")
    sp.fill("#chatf input", "Or Fondren lobby at 3?"); sp.click("#chatf button"); sp.wait_for_timeout(300)
    shot(sp, "11_deal_funded_seller_flagged_chat")

    # wrong code, then right code
    sp.fill("#hf input", "000000" if code != "000000" else "111111"); sp.click("#hf button.btn.big")
    sp.wait_for_selector(".toast.err"); shot(sp, "12_wrong_code")
    sp.fill("#hf input", code); sp.click("#hf button.btn.big"); sp.wait_for_selector("#actions .notice.green")
    shot(sp, "13_handed_off_seller")

    bp.reload(); bp.wait_for_selector("#confirm"); shot(bp, "14_handed_off_buyer")
    bp.click("#confirm"); bp.wait_for_selector("#stars"); shot(bp, "15_released")
    bp.click('#stars button[data-n="5"]'); bp.fill("#rc", "Exactly as pictured, easy meetup."); bp.click("#rate")
    bp.wait_for_selector(".toast"); bp.wait_for_timeout(500); shot(bp, "16_rated")

    # second deal -> dispute -> admin (bot user is first registered so is admin; log in as them)
    r = sp.request.post(BASE + "/api/listings", data={"title": "Two Rodeo tickets, Sec 112", "category": "ticket", "price": 85})
    lid2 = r.json()["listing_id"]; sp.request.post(BASE + f"/api/listings/{lid2}/photo", data={"photo_data": FAKE_PHOTO})
    bp.goto(BASE + f"/#/listing/{lid2}"); bp.click("#buy"); bp.wait_for_selector("#fund"); bp.click("#fund"); bp.wait_for_selector(".handoff-code")
    bp.click("#dispute"); bp.fill("#df textarea", "Seller no-showed twice at the agreed spot and stopped replying.")
    bp.click("#df button"); bp.wait_for_selector(".notice.red"); shot(bp, "17_disputed")

    xp.goto(BASE + "/#/admin"); xp.wait_for_selector(".stats"); shot(xp, "18_admin_disputes")
    xp.fill("input[id^=note-]", "Chat shows buyer proposed a time; seller never confirmed.")
    xp.click("button[id^=ref-]"); xp.wait_for_selector(".toast"); xp.wait_for_timeout(600); shot(xp, "19_admin_after_refund")

    bp.goto(BASE + f"/#/user/{3}"); bp.wait_for_selector("h1"); shot(bp, "20_profile")
    bp.set_viewport_size({"width": 390, "height": 844}); bp.goto(BASE + "/#/deals"); bp.wait_for_selector(".deal"); shot(bp, "21_mobile_deals")
    browser.close()
print("done")
