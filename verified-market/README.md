# Vouch — a campus marketplace where the stranger is already verified

Every account belongs to one verified human (Persona). Money never goes buyer → seller;
it goes buyer → escrow → seller (Capital One Nessie) and only moves after both people
have physically met and the buyer has confirmed.

Tracks/challenges: **Finance track**, **Persona "Prove you're human"**, **Capital One "Best Use of Nessie"**.

## Run it (60 seconds, no keys)

```bash
pip install -r requirements.txt
python app.py            # http://localhost:5000
```

Mock mode is the default: Persona shows a pass/fail switch (mirrors their sandbox toggle)
and Nessie is an in-memory ledger persisted to `mock_ledger.json`. Every flow works offline.

**The first account created is the admin** (sees the Disputes tab). Use separate browsers
or incognito windows for buyer / seller / admin — sessions are cookie-based.

## Turn on the real APIs

```bash
cp .env.example .env     # fill in keys
./run.sh
```

- **Nessie**: key from http://api.nessieisreal.com. Set `NESSIE_KEY` and `NESSIE_MOCK=0`.
  We use `POST /customers`, `POST /customers/{id}/accounts`, `GET /accounts/{id}`,
  `POST /accounts/{id}/transfers`. Every verified user gets a Nessie customer + checking
  account; every deal gets its own escrow account so the held money is visible as a real balance.
- **Persona**: sandbox template ID + environment ID (public, go in the browser) and an
  API key (secret, server only). The widget runs in the browser; the server re-checks the
  inquiry with `GET /api/v1/inquiries/{id}` so a client can't fake a pass. The Persona table
  at the hackathon will help wire this up — bring them `persona.py`.

## Demo script (2 minutes)

1. **Bot gets bounced.** Sign up, hit "Simulate a bot / fake ID" → can browse, can't sell or buy.
2. **Real seller lists.** New browser, verify as a real person → wallet opens with $500.
   Sell tab: write the proof word on paper, take the photo in-app (uploads are refused), publish.
3. **Real buyer pays.** Third browser, verify, buy the listing. Wallet drops, the ledger card
   shows the money sitting in **Held by Vouch**. Show the escrow balance is a real Nessie account.
4. **Handoff.** Buyer's screen shows a 6-digit code. Seller types the wrong code (rejected),
   then the right one → state moves to handed off. Both people had to be in the same room.
5. **Release.** Buyer confirms → money slides to the seller's wallet. Buyer rates the seller;
   point out the rating is bound to a completed deal between two verified identities.
6. **Dispute.** Buy a second listing, dispute it → escrow turns red and freezes. Admin tab:
   rule for the buyer, tick "ban" → refund lands, seller is locked out. Because identity is
   verified, the ban is on a person, not a throwaway account.
7. (Optional) Admin → "Fast-forward time" fires the auto-release / auto-refund timers.

## How it's built

```
app.py       Flask API + static hosting. All routes; the verification gate is a decorator.
store.py     SQLite schema + the escrow state machine. transition() is the ONLY way a deal
             changes state, and it rejects illegal moves (e.g. released → disputed).
nessie.py    Nessie client. Same interface in mock and live mode.
persona.py   Server-side inquiry check. Same interface in mock and live mode.
static/      index.html, styles.css, app.js — vanilla, no build step.
```

Deal states: `funded → handed_off → released`, with `disputed` reachable from either open
state and resolved by an admin to `released` or `refunded`. Timers: buyer has 24h after
handoff to confirm or dispute (then auto-release); seller has 7 days after funding to hand
off (then auto-refund). Timers are checked lazily on every read — no background worker needed.

## Anti-scam mechanisms, mapped to the threat

| Threat | Mechanism |
|---|---|
| Fake / throwaway accounts | Persona gate; one inquiry can verify one account |
| "Venmo me first" / seller ghosts | Escrow — seller sees funds are locked before handing anything over |
| Fake payment screenshots | Irrelevant — platform confirms funds, not the buyer |
| Stolen listing photos | In-app camera only + random proof word written in frame |
| Item not as described | 24h confirmation window; dispute freezes funds |
| Buyer holds seller's money hostage | Auto-release after window if no dispute |
| Fake handoff claim | 6-digit code only the buyer sees; seller must enter it in person |
| Review farming | Ratings only from completed escrow deals between two distinct verified people |
| Repeat offenders | Ban attaches to a verified identity, not a username |

## Questions judges will ask

- **Why a separate escrow account per deal?** So the held money is an auditable, visible
  balance rather than a number in our DB — you can point at it in Nessie.
- **What if a verified person scams once?** They can, with their legal identity attached.
  Escrow caps the damage to one transaction and the ban is permanent.
- **Who arbitrates disputes?** Today an admin with both sides' event history. Next step:
  tiered resolution and evidence uploads.
- **Isn't this Facebook Marketplace with extra steps?** The steps are the product. Facebook
  wins the $15 lamp; we win sublets, tickets and laptops — the deals people are scared to do.

## Not built (say so)

Document verification for sublet addresses (Persona supports it), in-app chat with
off-platform-payment detection, real payment rails, multi-photo listings.
