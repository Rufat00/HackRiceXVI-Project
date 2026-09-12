# Vouch

A campus marketplace where every account is a verified human and every payment sits in escrow until both people have met and the buyer is happy.

**Tracks/challenges:** Finance track · Persona "Prove you're human" · Capital One "Best Use of Nessie"

> Facebook trusts you to check out the stranger. Vouch checks out the stranger so you don't have to.

---

## Run it (60 seconds)

```bash
pip install -r requirements.txt
python seed.py        # demo users + 4 listings
python run.py         # http://localhost:5000
```

With no API keys set, Nessie and Persona both run in **mock mode**: a local JSON ledger stands in for Nessie, and a pass/fail toggle stands in for Persona's ID check. Every other line of code is identical to live mode. The footer badges tell you which mode you're in.

Demo accounts (password `demo123`):

| email | who |
|---|---|
| `admin@vouch.demo` | moderator — sees the **Disputes** tab |
| `sam@uh.edu` | verified seller, owns the 4 seeded listings |
| `bea@uh.edu` | verified buyer |
| `nate@uh.edu` | registered but **not** verified — for showing the gate |

Every new wallet starts with $500 of Nessie play money.

### Go live with the real sandboxes

```bash
export NESSIE_API_KEY=...                 # nessieisreal.com
export PERSONA_API_KEY=persona_sandbox_...   # Persona dashboard → API keys
export PERSONA_TEMPLATE_ID=itmpl_...         # Persona dashboard → Inquiry templates
export PERSONA_ENVIRONMENT_ID=env_...        # optional; defaults to environment="sandbox"
export PERSONA_WEBHOOK_SECRET=wbhsec_...     # optional; enables the /api/webhooks/persona path
python run.py
```

Setting `NESSIE_API_KEY` switches Nessie off mock automatically; setting both Persona vars does the same for Persona. Force either way with `NESSIE_MOCK=1/0`, `PERSONA_MOCK=1/0`.

For a demo you probably want the timers short so judges see auto-refund/auto-release fire:

```bash
HANDOFF_DEADLINE_SECONDS=120 CONFIRM_WINDOW_SECONDS=90 python run.py
```

---

## The 2-minute demo

Open two browsers (or one normal + one incognito). Seller in one, buyer in the other.

1. **Bounce a bot.** Create a fresh account → on the verify screen pick *Fail*. Rejected. Try *Pass* with a DOB under 18. Rejected. (In live mode Persona's sandbox has the same pass/fail switch inside its widget.)
2. **Verified buyer opens a listing.** Point at the proof word in the photo: "that word didn't exist until the seller hit *list*, so this can't be a stolen photo."
3. **Buy with escrow.** Buyer clicks *Pay into escrow*. The receipt on the right updates: money left the buyer's wallet, is sitting in a *per-transaction Nessie account*, seller hasn't got a cent. Show the six-digit handoff code.
4. **Seller tries the Venmo move.** In the seller's chat type "just venmo me @sam and I'll knock $5 off". It gets flagged inline and the buyer sees a warning.
5. **Handoff.** Seller types a wrong code → rejected, logged. Types the right one → state advances. Buyer's page now says *Everything's fine, release $40*.
6. **Release.** Buyer confirms. Receipt flips: escrow $0, seller +$40. Buyer leaves a 5★ rating — mention ratings can only come from completed escrow deals.
7. **Dispute path.** Start a second purchase, fund it, click *Something's wrong*. Everything freezes. Switch to the moderator, show the evidence panel (listing photo, chat, timeline), refund the buyer. Receipt shows the refund transfer.

If there's time: *Disputes → Run timers now* with short timers set, to show a ghosted seller getting auto-refunded.

---

## How it stops scams

| Threat | Defense | Where |
|---|---|---|
| Throwaway/fake accounts | Persona ID + selfie before listing or buying; result checked **server-side**, never trusted from the browser | `services/persona.py`, `routes/api.py::_apply_verification` |
| One person, many accounts | Persona account id (or name+DOB hash) stored as identity fingerprint; **DB unique index** rejects a second account | `db.py` users_identity_unique |
| Underage users | Age computed from Persona's birthdate; < 18 rejected | `_apply_verification` |
| "Pay first, I'll ship it" | Money goes buyer → escrow account → seller, never direct. Seller is told to hand over nothing until the page shows funds held | `services/escrow.py::fund` |
| Fake payment screenshots | Irrelevant: the platform confirms funds locked in Nessie before the seller sees "held" | `fund` |
| Seller ghosts after payment | Handoff deadline → automatic refund | `escrow.sweep` |
| Buyer ghosts after receiving item | Confirmation window → automatic release | `escrow.sweep` |
| Bait-and-switch / no actual meetup | Six-digit code shown to buyer, typed by seller in person. Stored as SHA-256, shown once | `fund`, `handoff` |
| Stolen listing photos / doesn't own the item | Random proof word assigned at listing time; photo must be captured with the in-app camera (no upload endpoint) | `create_listing`, `listing_photo`, `photoStep()` in app.js |
| High-value sublet fraud | Address required; deposits above $500 blocked until a lease/utility bill is shown to a moderator | `create_listing` |
| "Venmo me and skip the app" | Chat messages pattern-matched for payment apps, handles, phone/email, and pressure phrases; flagged inline, kept as evidence | `services/safety.py` |
| Fake reviews / reputation farming | Ratings only insertable on `released` transactions, one per rater, and rejected if both sides share an identity fingerprint | `escrow.rate` |
| Disputes | Freeze funds; moderator sees photo + chat + timeline and releases or refunds | `escrow.dispute/resolve`, admin page |

**What it doesn't solve (say this before a judge asks):** a verified person can still scam *once*, with their legal identity attached, and then they're gone for good — escrow limits the damage to one transaction. Item condition can't be verified digitally; that's what the confirmation window is for. Dispute resolution is a human; the MVP has a one-screen admin panel.

---

## Architecture

```
app/
  __init__.py          Flask app factory; wires Nessie + Persona clients into app.extensions
  config.py            env-driven; mock mode auto-selected when keys are missing
  db.py                sqlite3 schema (users, listings, transactions, events, messages, ratings)
  auth.py              session auth, login/verified/admin decorators, public user shape
  routes/api.py        JSON API (below)
  services/
    escrow.py          THE STATE MACHINE. fund/handoff/confirm/dispute/resolve/cancel + timers + reputation
    nessie.py          Capital One Nessie client + MockNessieClient with identical interface
    persona.py         Persona inquiry fetch, webhook signature check, MockPersonaClient
    safety.py          off-platform payment detector for chat
  static/
    index.html, styles.css, app.js   single-page frontend, hash router, no build step
seed.py                demo data
tests/test_flow.py     10 end-to-end tests of the whole flow in mock mode (pytest)
tests/ui_walkthrough.py  Playwright script that drives the demo and screenshots it
```

### Escrow states

```
created ──fund──▶ funded ──handoff code──▶ handed_off ──confirm / timer──▶ released
   │                 │                          │
   └─cancel─▶ cancelled                         ├──dispute──▶ disputed ──admin──▶ released | refunded
                     └──seller misses deadline─▶ refunded
```

Every transition writes a `transaction_events` row; that's the timeline both parties and the moderator see.

### Money in Nessie

- Each user gets a Nessie **customer** + **checking account** at registration.
- The platform has one Nessie customer ("Vouch Escrow"). Each funded transaction creates a **new account** under it, so the demo can point at a real account id whose balance is exactly the held amount.
- `fund` = transfer buyer → escrow. `release` = transfer escrow → seller. `refund` = transfer escrow → buyer. Transfer ids are stored on the transaction and shown on the receipt.

### Identity in Persona

- Frontend opens the embedded widget with `referenceId: "user:<id>"` and posts the returned `inquiryId`.
- Server fetches the inquiry (`GET /api/v1/inquiries/{id}`) and marks the user verified only if status is `completed`/`approved`. Legal name + birthdate come from the inquiry; nothing else is kept.
- The webhook endpoint verifies `Persona-Signature` (HMAC-SHA256 over `t.body`) and applies the same function, so either path can win.
- Identity fingerprint = Persona **account** id when present (Persona dedups the same human across inquiries), else `sha256(first|last|dob)`.

### API

```
POST /api/auth/register  /login  /logout        GET /api/me   GET /api/users/:id
POST /api/verify/complete                        POST /api/webhooks/persona
GET  /api/wallet         POST /api/wallet/topup  (mock only)
GET  /api/listings[?q=&category=]  GET /api/listings/:id  GET /api/listings/mine
POST /api/listings  (→ draft + proof word)   POST /api/listings/:id/photo (→ active)   DELETE /api/listings/:id
POST /api/listings/:id/buy
GET  /api/transactions   GET /api/transactions/:id
POST /api/transactions/:id/fund | handoff | confirm | cancel | dispute | rate
GET/POST /api/transactions/:id/messages
GET  /api/admin/disputes  /overview    POST /api/admin/transactions/:id/resolve   POST /api/admin/sweep
```

Timers run lazily on every API request (`@api.before_request`), so there's no worker process to babysit during judging.

---

## Things to check the first time you go live

These are the spots where a doc mismatch would bite; each is one function.

- **Nessie response shapes.** `NessieClient._created` reads `objectCreated._id`. If the sandbox returns something else, fix it there. Amounts are dollars (float) in Nessie; we convert from cents at the boundary.
- **Nessie transfers between customers.** We transfer buyer account → escrow account owned by a different customer. If Nessie rejects cross-customer transfers, fall back to: `deposit` into escrow + `withdrawal` from buyer (both endpoints exist) — it's the same two lines in `nessie.py`.
- **Persona SDK URL.** `app.js::loadPersona` loads `persona-v5.1.2.js` from `cdn.withpersona.com`. Check the current version in Persona's embedded-flow docs.
- **Persona status strings.** `PASSED_STATUSES = {"completed","approved"}` in `persona.py`. If your template uses a different terminal status, add it.
- **Camera needs HTTPS or localhost.** `getUserMedia` won't run over plain http on a LAN IP. For a phone demo, use `ngrok http 5000` or a localhost tunnel.

---

## Tests

```bash
pip install pytest && python -m pytest -q tests/test_flow.py
```

Covers: unverified users blocked, failed verification rejected, underage rejected, one-identity-one-account, full happy path with balances checked at each step, dispute → admin refund, both auto-timers, chat flagging, sublet address + cap, photo-must-be-in-app.
