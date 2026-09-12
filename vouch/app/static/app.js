/* Vouch frontend. Plain JS, hash router, talks to /api. */
(() => {
  "use strict";

  // ---------------------------------------------------------------- state
  const S = { me: null, cfg: null, freshCodes: {} };
  const $ = (sel, root = document) => root.querySelector(sel);
  const view = $("#view");

  const money = (c) => "$" + (c / 100).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (m) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m]));
  const when = (s) => (s ? new Date(s.replace(" ", "T") + (s.endsWith("Z") ? "" : "Z")).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "");
  const untilText = (s) => {
    if (!s) return "";
    const ms = new Date(s.replace(" ", "T") + "Z") - Date.now();
    if (ms <= 0) return "any moment now";
    const m = Math.round(ms / 60000);
    if (m < 60) return `${m} min`;
    const h = Math.round(m / 60);
    return h < 48 ? `${h} h` : `${Math.round(h / 24)} days`;
  };

  // ---------------------------------------------------------------- api
  async function api(path, opts = {}) {
    const r = await fetch("/api" + path, {
      method: opts.method || (opts.body ? "POST" : "GET"),
      headers: { "Content-Type": "application/json" },
      body: opts.body ? JSON.stringify(opts.body) : undefined,
      credentials: "same-origin",
    });
    let data = {};
    try { data = await r.json(); } catch (_) { /* empty */ }
    if (!r.ok) {
      const e = new Error(data.error || `Request failed (${r.status})`);
      e.status = r.status; e.code = data.code; e.data = data;
      throw e;
    }
    return data;
  }

  // ---------------------------------------------------------------- toast/modal
  let toastTimer;
  function toast(msg, isErr = false) {
    const t = $("#toast");
    t.textContent = msg; t.className = "toast show" + (isErr ? " err" : "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (t.className = "toast"), isErr ? 5000 : 3000);
  }
  function modal(html) {
    const root = $("#modal-root");
    root.innerHTML = `<div class="modal-back" data-close><div class="modal" role="dialog" aria-modal="true">${html}</div></div>`;
    root.querySelector("[data-close]").addEventListener("click", (e) => { if (e.target.hasAttribute("data-close")) closeModal(); });
    return root.querySelector(".modal");
  }
  function closeModal() { $("#modal-root").innerHTML = ""; }
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

  // ---------------------------------------------------------------- header
  async function refreshMe() {
    const [me, cfg] = await Promise.all([api("/me"), S.cfg ? Promise.resolve(S.cfg) : api("/config")]);
    S.me = me.user; S.cfg = cfg;
    renderHeader();
  }
  function renderHeader() {
    const me = S.me, cfg = S.cfg;
    const links = [["#/", "Browse"], ["#/sell", "Sell"], ["#/deals", "My deals"]];
    if (me && me.is_admin) links.push(["#/admin", "Disputes"]);
    const here = location.hash || "#/";
    $("#nav").innerHTML = links.map(([h, t]) => `<a href="${h}" class="${here === h || (h !== "#/" && here.startsWith(h)) ? "on" : ""}">${t}</a>`).join("");
    if (!me) {
      $("#me").innerHTML = `<button class="btn quiet sm" id="signin">Sign in</button><button class="btn sm" id="signup">Create account</button>`;
      $("#signin").onclick = () => authModal("login");
      $("#signup").onclick = () => authModal("register");
    } else {
      const bal = me.wallet && me.wallet.balance_cents != null ? money(me.wallet.balance_cents) : "—";
      $("#me").innerHTML = `
        <span class="wallet">Wallet <strong>${bal}</strong></span>
        <a class="who" href="#/user/${me.id}">${esc(me.display_name)} ${badge(me.verified)}</a>
        <button class="btn quiet sm" id="signout">Sign out</button>`;
      $("#signout").onclick = async () => { await api("/auth/logout", { body: {} }); S.me = null; renderHeader(); route(); };
    }
    $("#mode-badges").innerHTML = `${cfg.nessie_mock ? '<span class="chip neutral">Nessie: local ledger</span>' : '<span class="chip ok">Nessie: live sandbox</span>'} ${cfg.persona_mock ? '<span class="chip neutral">Persona: mock</span>' : '<span class="chip ok">Persona: live sandbox</span>'}`;
  }
  const badge = (v) => v ? `<span class="chip verified" title="Identity verified by Persona">✓ Verified</span>` : `<span class="chip unverified">Unverified</span>`;

  // ---------------------------------------------------------------- auth
  function authModal(tab = "login", after) {
    const m = modal(`
      <div class="tabs"><button data-t="login" class="${tab === "login" ? "on" : ""}">Sign in</button><button data-t="register" class="${tab === "register" ? "on" : ""}">Create account</button></div>
      <form id="authf">
        <div class="field" id="namef" ${tab === "login" ? "hidden" : ""}><label>Display name</label><input name="display_name" placeholder="How others see you" autocomplete="nickname"></div>
        <div class="field"><label>Email</label><input name="email" type="email" required autocomplete="email"></div>
        <div class="field"><label>Password</label><input name="password" type="password" required minlength="6" autocomplete="current-password"></div>
        <p class="small" ${tab === "login" ? "hidden" : ""} id="regnote">Creating an account opens a wallet with $500 of Nessie play money. You'll verify your identity next.</p>
        <button class="btn" style="width:100%">${tab === "login" ? "Sign in" : "Create account"}</button>
      </form>`);
    m.querySelectorAll(".tabs button").forEach((b) => (b.onclick = () => authModal(b.dataset.t, after)));
    $("#authf", m).onsubmit = async (e) => {
      e.preventDefault();
      const f = Object.fromEntries(new FormData(e.target));
      try {
        await api(tab === "login" ? "/auth/login" : "/auth/register", { body: f });
        closeModal(); await refreshMe();
        if (tab === "register" && !S.me.verified) location.hash = "#/verify";
        else if (after) after(); else route();
      } catch (err) { toast(err.message, true); }
    };
  }
  function requireUser(after) {
    if (S.me) return true;
    authModal("login", after);
    return false;
  }
  function requireVerified() {
    if (!requireUser()) return false;
    if (!S.me.verified) { location.hash = "#/verify"; return false; }
    return true;
  }

  // ---------------------------------------------------------------- pages
  const pages = {};

  pages.home = async () => {
    const params = new URLSearchParams((location.hash.split("?")[1] || ""));
    const q = params.get("q") || "", cat = params.get("category") || "";
    const { listings } = await api(`/listings?q=${encodeURIComponent(q)}&category=${cat}`);
    view.innerHTML = `
      ${!S.me ? `<section class="hero">
        <div>
          <h1>Buy and sell with strangers like they're friends.</h1>
          <p>Every account on Vouch belongs to a verified, real person. Your money sits in escrow until you've met and you're happy. The other side can't take it and vanish — and neither can you.</p>
          <div class="row"><button class="btn big" id="hero-join">Create an account</button><a class="btn quiet big" href="#/sell">See how selling works</a></div>
          <div class="how">
            <div><strong>Prove you're a person</strong><span>A 2-minute ID check with Persona. One human, one account, 18+.</span></div>
            <div><strong>Money waits in escrow</strong><span>The buyer pays into a holding account neither side controls.</span></div>
            <div><strong>Meet, hand off, release</strong><span>A one-time code proves you actually met before anything moves.</span></div>
          </div>
        </div>
        <div class="receipt" aria-hidden="true">
          <div class="head"><h3>Escrow receipt</h3><span class="chip held">Held</span></div>
          <div class="amount">$85.00</div>
          <div class="flow"><div class="done"><strong>Buyer</strong>paid</div><div class="here"><strong>Escrow</strong>holding</div><div><strong>Seller</strong>waiting</div></div>
          <dl><dt>Item</dt><dd>Two Rodeo tickets, Sec 112</dd><dt>Buyer</dt><dd>Priya ✓</dd><dt>Seller</dt><dd>Marcus ✓</dd><dt>Releases when</dt><dd>Priya confirms, or 24 h after handoff</dd></dl>
        </div>
      </section>` : ""}
      <div class="spread"><h2>${S.me ? "What's for sale" : "Listed right now"}</h2><a class="btn sm" href="#/sell">List something</a></div>
      <form class="filters" id="filters">
        <input name="q" placeholder="Search textbooks, tickets, sublets…" value="${esc(q)}">
        <select name="category"><option value="">All categories</option>${["item", "ticket", "sublet"].map((c) => `<option value="${c}" ${cat === c ? "selected" : ""}>${cap(c)}s</option>`).join("")}</select>
        <button class="btn quiet">Search</button>
      </form>
      ${listings.length ? `<div class="grid">${listings.map(listingCard).join("")}</div>` : `<div class="empty">Nothing listed${q || cat ? " for that search" : " yet"}. <a href="#/sell">Be the first to list something.</a></div>`}`;
    const hj = $("#hero-join"); if (hj) hj.onclick = () => authModal("register");
    $("#filters").onsubmit = (e) => { e.preventDefault(); const f = new FormData(e.target); location.hash = `#/?q=${encodeURIComponent(f.get("q"))}&category=${f.get("category")}`; };
  };
  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
  const listingCard = (l) => `
    <a class="card" href="#/listing/${l.id}">
      ${l.photo_data ? `<img src="${l.photo_data}" alt="">` : `<div class="photo"></div>`}
      <div class="body">
        <span class="price">${money(l.price_cents)}</span>
        <span class="title">${esc(l.title)}</span>
        <span class="seller">${esc(l.seller_name)} ${badge(l.seller_verified)} ${repText(l.seller_reputation)}</span>
      </div>
    </a>`;
  const repText = (r) => (r && r.ratings ? `· ${r.avg_stars}★ (${r.ratings})` : r && r.completed_transactions ? `· ${r.completed_transactions} completed` : "· new");

  pages.listing = async (id) => {
    const { listing: l } = await api(`/listings/${id}`);
    const mine = S.me && S.me.id === l.seller_id;
    view.innerHTML = `
      <div class="two">
        <div class="stack">
          ${l.photo_data ? `<img class="detail-photo" src="${l.photo_data}" alt="Photo of ${esc(l.title)} taken by the seller in-app">` : ""}
          <div>
            <h1>${esc(l.title)}</h1>
            <p class="small">${cap(l.category)} · listed ${when(l.created_at)}${l.address ? ` · ${esc(l.address)}` : ""}</p>
            <p>${esc(l.description) || "<span class='small'>No description.</span>"}</p>
          </div>
          <div class="notice green"><strong>Why you can trust this photo.</strong> It was taken with the in-app camera at listing time and shows a word we assigned the seller on the spot, so it can't be lifted from another listing.</div>
        </div>
        <aside class="stack">
          <div class="panel">
            <div class="amount" style="font-size:2rem;font-weight:700">${money(l.price_cents)}</div>
            <p><a href="#/user/${l.seller_id}">${esc(l.seller_name)}</a> ${badge(l.seller_verified)}<br><span class="small">${repText(l.seller_reputation).replace("· ", "")}</span></p>
            ${mine ? `<p class="small">This is your listing.</p><button class="btn danger" id="remove">Remove listing</button>`
              : l.status !== "active" ? `<p class="small">This listing is ${l.status}.</p>`
              : `<button class="btn big" id="buy" style="width:100%">Buy with escrow</button>
                 <p class="small" style="margin-top:.75rem">You'll pay into escrow. The seller only gets paid after you meet and confirm.</p>`}
          </div>
        </aside>
      </div>`;
    const b = $("#buy");
    if (b) b.onclick = async () => {
      if (!requireVerified()) return;
      try {
        const { transaction } = await api(`/listings/${l.id}/buy`, { body: {} });
        location.hash = `#/deal/${transaction.id}`;
      } catch (e) { toast(e.message, true); }
    };
    const rm = $("#remove");
    if (rm) rm.onclick = async () => { try { await api(`/listings/${l.id}`, { method: "DELETE" }); toast("Listing removed"); location.hash = "#/"; } catch (e) { toast(e.message, true); } };
  };

  // ---------------------------------------------------------------- verify
  pages.verify = async () => {
    if (!requireUser(() => (location.hash = "#/verify"))) { view.innerHTML = ""; return; }
    if (S.me.verified) {
      view.innerHTML = `<div class="panel" style="max-width:560px"><h1>You're verified</h1><p>Verified ${when(S.me.verified_at)}. You can list and buy.</p><a class="btn" href="#/">Browse listings</a></div>`;
      return;
    }
    const cfg = S.cfg;
    view.innerHTML = `
      <div class="two">
        <div class="stack">
          <h1>Prove you're a person</h1>
          <p>Vouch only works if every account is a real human, exactly once. We use <a href="https://withpersona.com" target="_blank" rel="noopener">Persona</a> to check a government ID and a selfie. It takes about two minutes.</p>
          <p class="small">We keep only your legal first name, whether you passed, and a fingerprint that stops the same person from opening a second account. Your ID photos stay with Persona.</p>
          <div class="panel" id="vbox"></div>
        </div>
        <aside class="stack">
          <div class="panel">
            <h3>What verification unlocks</h3>
            <p class="small">Listing items, buying with escrow, chatting with the other side, leaving ratings. Browsing works without it.</p>
            <h3>What it blocks</h3>
            <p class="small">Throwaway accounts, one person running five sellers, anyone under ${cfg.min_age}.</p>
          </div>
        </aside>
      </div>`;
    const box = $("#vbox");
    if (cfg.persona_mock) {
      box.innerHTML = `
        <div class="notice">Sandbox mode. No Persona keys are set, so this toggle stands in for the ID check. The server treats the result exactly as it would a real inquiry.</div>
        <form id="mockf" style="margin-top:1rem">
          <div class="inline">
            <div class="field"><label>Legal first name</label><input name="first_name" value="${esc(S.me.display_name.split(" ")[0])}"></div>
            <div class="field"><label>Legal last name</label><input name="last_name" value="Student"></div>
          </div>
          <div class="field"><label>Date of birth</label><input name="birthdate" type="date" value="2004-03-15"><span class="hint">Try a date under 18 to see the age gate.</span></div>
          <div class="field"><label>Simulated outcome</label>
            <select name="outcome"><option value="pass">Pass — real person, ID matches selfie</option><option value="fail">Fail — bot, mismatch, or fake ID</option></select></div>
          <button class="btn big">Run verification</button>
        </form>`;
      $("#mockf").onsubmit = async (e) => {
        e.preventDefault();
        try {
          await api("/verify/complete", { body: Object.fromEntries(new FormData(e.target)) });
          await refreshMe(); toast("Verified. Welcome to Vouch."); location.hash = "#/";
        } catch (err) { toast(err.message, true); }
      };
    } else {
      box.innerHTML = `<button class="btn big" id="start">Start ID check</button><p class="small" style="margin-top:.75rem">Opens Persona's secure window. Sandbox: pick "Simulate pass" or "Simulate fail" inside it.</p>`;
      $("#start").onclick = () => loadPersona().then(() => {
        const client = new window.Persona.Client({
          templateId: cfg.persona_template_id,
          environmentId: cfg.persona_environment_id || undefined,
          environment: cfg.persona_environment_id ? undefined : cfg.persona_environment,
          referenceId: `user:${S.me.id}`,
          onReady: () => client.open(),
          onComplete: async ({ inquiryId }) => {
            try {
              await api("/verify/complete", { body: { inquiry_id: inquiryId } });
              await refreshMe(); toast("Verified. Welcome to Vouch."); location.hash = "#/";
            } catch (err) { toast(err.message, true); }
          },
          onCancel: () => toast("Verification cancelled"),
          onError: (err) => toast("Persona error: " + (err && err.code), true),
        });
      }).catch((e) => toast(e.message, true));
    }
  };
  function loadPersona() {
    if (window.Persona) return Promise.resolve();
    return new Promise((res, rej) => {
      const s = document.createElement("script");
      s.src = "https://cdn.withpersona.com/dist/persona-v5.1.2.js";
      s.onload = res; s.onerror = () => rej(new Error("Couldn't load the Persona SDK — check the CDN URL in app.js against docs.withpersona.com"));
      document.head.appendChild(s);
    });
  }

  // ---------------------------------------------------------------- sell
  pages.sell = async () => {
    if (!S.me) {
      view.innerHTML = `<div class="stack" style="max-width:640px">
        <h1>Selling on Vouch</h1>
        <p>List in three steps: describe the item, photograph it with the in-app camera showing a word we give you, and it goes live. When a buyer pays, the money lands in escrow and you get told to arrange a meetup. At the meetup they show you a six-digit code; you type it in, and the money releases to you once they confirm (or automatically 24 hours later).</p>
        <p>You never hand anything over on a promise, and the buyer never pays into thin air.</p>
        <button class="btn big" id="go">Create an account to sell</button></div>`;
      $("#go").onclick = () => authModal("register");
      return;
    }
    if (!S.me.verified) { location.hash = "#/verify"; return; }
    const cfg = S.cfg;
    view.innerHTML = `
      <div class="two">
        <div class="stack">
          <h1>List something</h1>
          <form id="sellf" class="panel">
            <div class="field"><label>Title</label><input name="title" required maxlength="80" placeholder="e.g. Stewart Calculus 8e, hardcover"></div>
            <div class="inline">
              <div class="field"><label>Category</label><select name="category" id="cat"><option value="item">Item</option><option value="ticket">Ticket</option><option value="sublet">Sublet</option></select></div>
              <div class="field"><label>Price (USD)</label><input name="price" type="number" min="1" step="0.01" required placeholder="40.00"></div>
            </div>
            <div class="field" id="addrf" hidden><label>Address</label><input name="address" placeholder="Street address of the unit"><span class="hint">Deposits above ${money(cfg.sublet_unverified_cap_cents)} need a lease or utility bill shown to a moderator.</span></div>
            <div class="field"><label>Description</label><textarea name="description" maxlength="1000" placeholder="Condition, edition, section, move-in dates…"></textarea></div>
            <button class="btn big">Continue to photo</button>
          </form>
        </div>
        <aside class="stack">
          <div class="panel">
            <h3>Next: the proof photo</h3>
            <p class="small">We'll give you a random word. Write it on paper, put it next to the item, and take the photo in-app. No uploads — that's what stops people listing things they don't have.</p>
          </div>
        </aside>
      </div>`;
    $("#cat").onchange = (e) => ($("#addrf").hidden = e.target.value !== "sublet");
    $("#sellf").onsubmit = async (e) => {
      e.preventDefault();
      const f = Object.fromEntries(new FormData(e.target));
      try {
        const r = await api("/listings", { body: f });
        photoStep(r.listing_id, r.proof_word);
      } catch (err) { toast(err.message, true); }
    };
  };

  function photoStep(listingId, word) {
    view.innerHTML = `
      <div class="two">
        <div class="stack">
          <h1>Prove you have it</h1>
          <p>Write this word on a piece of paper, put it beside the item, and take the photo.</p>
          <div class="proof-word" aria-label="Your proof word">${esc(word)}</div>
          <div class="camera" id="cam"><video autoplay playsinline muted></video><canvas hidden></canvas><div class="overlay">Frame the item and the word together.</div></div>
          <div class="row">
            <button class="btn big" id="snap">Take photo</button>
            <button class="btn quiet" id="retake" hidden>Retake</button>
            <button class="btn" id="publish" hidden>Publish listing</button>
          </div>
          <div id="camerr"></div>
        </div>
        <aside class="stack"><div class="panel"><h3>Why the word?</h3><p class="small">A stolen photo from another site can't contain a word we only just generated. It's a cheap proof of possession that judges (and buyers) can see at a glance.</p></div></aside>
      </div>`;
    const cam = $("#cam"), video = $("video", cam), canvas = $("canvas", cam);
    let stream, dataUrl;
    navigator.mediaDevices?.getUserMedia({ video: { facingMode: "environment" }, audio: false })
      .then((s) => { stream = s; video.srcObject = s; })
      .catch(() => {
        $("#camerr").innerHTML = `<div class="notice red">Camera unavailable. On a phone, use the capture button below (it opens the camera directly, not the gallery).<br><input type="file" accept="image/*" capture="environment" id="capfile" style="margin-top:.5rem"></div>`;
        $("#capfile").onchange = (e) => {
          const f = e.target.files[0]; if (!f) return;
          const rd = new FileReader(); rd.onload = () => { dataUrl = rd.result; showShot(); }; rd.readAsDataURL(f);
        };
      });
    function showShot() {
      video.hidden = true; canvas.hidden = true;
      const img = $("img", cam) || cam.insertBefore(document.createElement("img"), $(".overlay", cam));
      img.src = dataUrl; $("#snap").hidden = true; $("#retake").hidden = false; $("#publish").hidden = false;
    }
    $("#snap").onclick = () => {
      if (!stream) return toast("Camera isn't ready yet", true);
      canvas.width = video.videoWidth; canvas.height = video.videoHeight;
      canvas.getContext("2d").drawImage(video, 0, 0);
      dataUrl = canvas.toDataURL("image/jpeg", 0.8); showShot();
    };
    $("#retake").onclick = () => { const img = $("img", cam); if (img) img.remove(); video.hidden = false; $("#snap").hidden = false; $("#retake").hidden = true; $("#publish").hidden = true; };
    $("#publish").onclick = async () => {
      try {
        await api(`/listings/${listingId}/photo`, { body: { photo_data: dataUrl } });
        stream?.getTracks().forEach((t) => t.stop());
        toast("Published"); location.hash = `#/listing/${listingId}`;
      } catch (e) { toast(e.message, true); }
    };
    window.addEventListener("hashchange", () => stream?.getTracks().forEach((t) => t.stop()), { once: true });
  }

  // ---------------------------------------------------------------- deals
  const STATE_TEXT = {
    created: ["Awaiting payment", "neutral"], funded: ["Held in escrow", "held"], handed_off: ["Handed off", "held"],
    released: ["Paid to seller", "ok"], refunded: ["Refunded", "ok"], disputed: ["Disputed", "bad"], cancelled: ["Cancelled", "neutral"],
  };
  const stateChip = (s) => `<span class="chip ${STATE_TEXT[s][1]}">${STATE_TEXT[s][0]}</span>`;

  pages.deals = async () => {
    if (!requireUser(() => (location.hash = "#/deals"))) { view.innerHTML = ""; return; }
    const [{ transactions }, { listings }] = await Promise.all([api("/transactions"), api("/listings/mine")]);
    const open = transactions.filter((t) => ["created", "funded", "handed_off", "disputed"].includes(t.state));
    const closed = transactions.filter((t) => !open.includes(t));
    view.innerHTML = `
      <h1>My deals</h1>
      <div class="two">
        <div class="stack">
          <h2>In progress</h2>
          ${open.length ? open.map(dealRow).join("") : `<div class="empty">No open deals. <a href="#/">Find something</a> or <a href="#/sell">list something</a>.</div>`}
          <h2 style="margin-top:2rem">Finished</h2>
          ${closed.length ? closed.map(dealRow).join("") : `<div class="empty">Nothing finished yet.</div>`}
        </div>
        <aside class="stack">
          <div class="panel">
            <div class="spread"><h3>My listings</h3><a class="btn sm quiet" href="#/sell">New</a></div>
            ${listings.length ? listings.map((l) => `<p><a href="#/listing/${l.id}">${esc(l.title)}</a> <span class="small">${money(l.price_cents)} · ${l.status}</span></p>`).join("") : `<p class="small">None yet.</p>`}
          </div>
          ${S.cfg.nessie_mock ? `<div class="panel"><h3>Wallet</h3><p class="small">Play money for the demo.</p><button class="btn quiet sm" id="topup">Add $100</button></div>` : ""}
        </aside>
      </div>`;
    const tu = $("#topup"); if (tu) tu.onclick = async () => { await api("/wallet/topup", { body: { amount_cents: 10000 } }); await refreshMe(); toast("Added $100"); };
  };
  const dealRow = (t) => `
    <a class="deal" href="#/deal/${t.id}">
      <div><span class="t">${esc(t.listing.title)}</span><br><span class="small">${t.role === "buyer" ? "Buying from " + esc(t.seller.display_name) : "Selling to " + esc(t.buyer.display_name)} · ${when(t.created_at)}</span></div>
      <div style="text-align:right"><strong>${money(t.amount_cents)}</strong><br>${stateChip(t.state)}</div>
    </a>`;

  pages.deal = async (id) => {
    if (!requireUser(() => (location.hash = `#/deal/${id}`))) { view.innerHTML = ""; return; }
    const { transaction: t } = await api(`/transactions/${id}`);
    const me = S.me, role = t.role, other = role === "buyer" ? t.seller : t.buyer;
    const freshCode = S.freshCodes[t.id];
    view.innerHTML = `
      <p class="small"><a href="#/deals">My deals</a> / #${t.id}</p>
      <div class="two">
        <div class="stack">
          <div class="spread"><h1>${esc(t.listing.title)}</h1>${stateChip(t.state)}</div>
          <p class="small">${role === "buyer" ? "You're buying from" : "You're selling to"} <a href="#/user/${other.id}">${esc(other.display_name)}</a> ${badge(other.verified)}</p>
          <div id="actions" class="stack"></div>
          <div class="panel">
            <h3>Messages</h3>
            <p class="small">Keep it here. Anything arranged outside Vouch isn't protected, and we flag messages that push for outside payment.</p>
            <div class="chat" id="chat"></div>
            <form class="chat-form" id="chatf"><input name="body" placeholder="Where and when do you want to meet?" autocomplete="off" ${["released", "refunded", "cancelled"].includes(t.state) ? "disabled" : ""}><button class="btn" ${["released", "refunded", "cancelled"].includes(t.state) ? "disabled" : ""}>Send</button></form>
          </div>
          <div class="panel"><h3>History</h3><ul class="timeline">${t.events.map((e) => `<li class="${/failed|disputed/.test(e.kind) ? "warn" : ""}"><div><strong>${esc(eventLabel(e.kind))}</strong>${e.detail ? ` — ${esc(e.detail)}` : ""}<time>${when(e.created_at)}${e.actor_name ? " · " + esc(e.actor_name) : " · Vouch"}</time></div></li>`).join("")}</ul></div>
        </div>
        <aside class="stack">${receipt(t)}</aside>
      </div>`;
    renderActions(t, freshCode);
    await loadChat(t);
    $("#chatf").onsubmit = async (e) => {
      e.preventDefault();
      const body = e.target.body.value.trim(); if (!body) return;
      try {
        const r = await api(`/transactions/${t.id}/messages`, { body: { body } });
        e.target.reset(); await loadChat(t);
        if (r.flagged) toast("Heads up: that message was flagged — " + r.flag_reason, true);
      } catch (err) { toast(err.message, true); }
    };
  };
  const eventLabel = (k) => ({ created: "Purchase started", funded: "Paid into escrow", handoff_failed: "Wrong code entered", handed_off: "Handoff confirmed", released: "Money released", refunded: "Money refunded", disputed: "Dispute opened", cancelled: "Cancelled" }[k] || k);

  function receipt(t) {
    const s = t.state;
    const stage = (name, cls, sub) => `<div class="${cls}"><strong>${name}</strong>${sub}</div>`;
    let flow;
    if (s === "created") flow = stage("Buyer", "here", "to pay") + stage("Escrow", "", "empty") + stage("Seller", "", "waiting");
    else if (s === "funded") flow = stage("Buyer", "done", "paid") + stage("Escrow", "here", "holding") + stage("Seller", "", "waiting");
    else if (s === "handed_off") flow = stage("Buyer", "done", "paid") + stage("Escrow", "here", "holding") + stage("Seller", "", "handed off");
    else if (s === "released") flow = stage("Buyer", "done", "paid") + stage("Escrow", "done", "released") + stage("Seller", "done", "received");
    else if (s === "refunded") flow = stage("Buyer", "done", "refunded") + stage("Escrow", "done", "returned") + stage("Seller", "", "—");
    else if (s === "disputed") flow = stage("Buyer", "done", "paid") + stage("Escrow", "frozen", "frozen") + stage("Seller", "", "waiting");
    else flow = stage("Buyer", "", "—") + stage("Escrow", "", "—") + stage("Seller", "", "—");
    const bal = t.escrow_account && t.escrow_account.balance_cents != null ? money(t.escrow_account.balance_cents) : "—";
    const nextLine = s === "funded" ? `<dt>If no handoff</dt><dd>Refund in ${untilText(t.handoff_deadline)}</dd>`
      : s === "handed_off" ? `<dt>Auto-release</dt><dd>In ${untilText(t.confirm_deadline)}</dd>` : "";
    return `<div class="receipt">
      <div class="head"><h3>Escrow receipt</h3>${stateChip(s)}</div>
      <div class="amount">${money(t.amount_cents)}</div>
      <div class="flow">${flow}</div>
      <dl>
        <dt>Buyer</dt><dd>${esc(t.buyer.display_name)} ${t.buyer.verified ? "✓" : ""}</dd>
        <dt>Seller</dt><dd>${esc(t.seller.display_name)} ${t.seller.verified ? "✓" : ""}</dd>
        ${nextLine}
      </dl>
      <hr class="rule">
      <dl>
        <dt>Escrow account</dt><dd>${t.escrow_account_id ? `<span title="${esc(t.escrow_account_id)}">${esc(t.escrow_account_id).slice(0, 14)}…</span>` : "not yet opened"}</dd>
        <dt>Balance now</dt><dd><strong>${bal}</strong></dd>
        ${t.fund_transfer_id ? `<dt>Funding transfer</dt><dd>${esc(t.fund_transfer_id).slice(0, 14)}…</dd>` : ""}
        ${t.release_transfer_id ? `<dt>${s === "refunded" ? "Refund" : "Release"} transfer</dt><dd>${esc(t.release_transfer_id).slice(0, 14)}…</dd>` : ""}
        ${t.resolution ? `<dt>Closed because</dt><dd>${esc(t.resolution)}</dd>` : ""}
      </dl>
      <hr class="rule">
      <p class="small" style="margin:0">Account ids are real Capital One Nessie objects${S.cfg.nessie_mock ? " (local ledger in this demo)" : ""}. Neither party can withdraw from escrow.</p>
    </div>`;
  }

  function renderActions(t, freshCode) {
    const a = $("#actions"), s = t.state, role = t.role;
    const act = async (path, body, okMsg) => {
      try { const r = await api(`/transactions/${t.id}/${path}`, { body: body || {} }); if (r.handoff_code) S.freshCodes[t.id] = r.handoff_code; await refreshMe(); toast(okMsg); route(); }
      catch (e) { toast(e.message, true); }
    };
    if (s === "created" && role === "buyer") {
      a.innerHTML = `<div class="panel"><h3>Pay ${money(t.amount_cents)} into escrow</h3><p>The money leaves your wallet and sits in a holding account until you've met the seller and confirmed. If they never show, it comes back automatically.</p><div class="row"><button class="btn big" id="fund">Pay into escrow</button><button class="btn quiet" id="cancel">Cancel</button></div></div>`;
      $("#fund").onclick = () => act("fund", {}, "Paid into escrow");
      $("#cancel").onclick = () => act("cancel", {}, "Cancelled");
    } else if (s === "created") {
      a.innerHTML = `<div class="notice">Waiting for ${esc(t.buyer.display_name)} to pay into escrow. Don't hand anything over until this page says the money is held.</div>`;
    } else if (s === "funded" && role === "buyer") {
      a.innerHTML = `<div class="panel"><h3>Your handoff code</h3><p>Show this to ${esc(t.seller.display_name)} when you meet and have the item in hand. They type it in; that's what proves you actually met.</p>
        ${freshCode ? `<div class="handoff-code">${freshCode}</div><p class="small">Save it — for safety we don't store the code in plain text, so it's only shown on this device.</p>` : `<div class="notice">The code was shown when you paid and isn't stored on our servers. If you lost it, open a dispute and a moderator can reissue.</div>`}
        <div class="row" style="margin-top:1rem"><button class="btn danger" id="dispute">Something's wrong</button></div></div>`;
      $("#dispute").onclick = () => disputeModal(t);
    } else if (s === "funded") {
      a.innerHTML = `<div class="panel"><h3>${money(t.amount_cents)} is held for you</h3><p>Meet ${esc(t.buyer.display_name)}, hand over the item, and type the six-digit code they show you.</p>
        <form id="hf"><input class="code-input" name="code" inputmode="numeric" pattern="\\d{6}" maxlength="6" placeholder="••••••" required><div class="row" style="margin-top:.75rem"><button class="btn big">Confirm handoff</button><button type="button" class="btn danger" id="dispute">Something's wrong</button></div></form></div>`;
      $("#hf").onsubmit = (e) => { e.preventDefault(); act("handoff", { code: e.target.code.value }, "Handoff confirmed"); };
      $("#dispute").onclick = () => disputeModal(t);
    } else if (s === "handed_off" && role === "buyer") {
      a.innerHTML = `<div class="panel"><h3>Got it? Release the money.</h3><p>Check the item now. Confirming pays ${esc(t.seller.display_name)} immediately. If you do nothing, it releases automatically in ${untilText(t.confirm_deadline)}.</p><div class="row"><button class="btn big" id="confirm">Everything's fine, release ${money(t.amount_cents)}</button><button class="btn danger" id="dispute">Not as described</button></div></div>`;
      $("#confirm").onclick = () => act("confirm", {}, "Released to seller");
      $("#dispute").onclick = () => disputeModal(t);
    } else if (s === "handed_off") {
      a.innerHTML = `<div class="notice green">Handoff confirmed. ${esc(t.buyer.display_name)} has until ${when(t.confirm_deadline)} to flag a problem; otherwise the money releases to you automatically.</div>`;
    } else if (s === "disputed") {
      a.innerHTML = `<div class="notice red"><strong>Frozen.</strong> ${esc(t.dispute_opened_by === t.buyer_id ? t.buyer.display_name : t.seller.display_name)} opened a dispute: “${esc(t.dispute_reason)}”. A Vouch moderator will review the listing photo, the chat, and the timeline, then release or refund.</div>`;
    } else if (s === "released") {
      a.innerHTML = `<div class="panel"><h3>Done. ${money(t.amount_cents)} went to ${esc(t.seller.display_name)}.</h3>${t.my_rating ? `<p class="small">You rated ${esc(other(t).display_name)} ${t.my_rating.stars}★.</p>` : `<p>How was ${esc(other(t).display_name)}? Ratings only come from completed escrow deals, so they mean something.</p><div class="stars" id="stars">${[1, 2, 3, 4, 5].map((n) => `<button data-n="${n}" aria-label="${n} stars">★</button>`).join("")}</div><input id="rc" placeholder="Optional comment" style="width:100%;margin:.5rem 0;padding:.5rem"><button class="btn" id="rate" disabled>Submit rating</button>`}</div>`;
      let n = 0;
      const st = $("#stars"); if (st) st.querySelectorAll("button").forEach((b) => (b.onclick = () => { n = +b.dataset.n; st.querySelectorAll("button").forEach((x) => x.classList.toggle("on", +x.dataset.n <= n)); $("#rate").disabled = false; }));
      const rb = $("#rate"); if (rb) rb.onclick = () => act("rate", { stars: n, comment: $("#rc").value }, "Thanks for rating");
    } else {
      a.innerHTML = `<div class="notice">This deal is ${s}.${t.resolution ? " " + esc(t.resolution) : ""}</div>`;
    }
  }
  const other = (t) => (t.role === "buyer" ? t.seller : t.buyer);

  function disputeModal(t) {
    const m = modal(`<h2>What went wrong?</h2><p class="small">This freezes the money. A moderator will look at the listing photo, your messages, and the timeline. Be specific.</p>
      <form id="df"><div class="field"><textarea name="reason" required minlength="10" placeholder="e.g. Seller didn't show at the agreed time twice / Item is a different edition than pictured"></textarea></div><button class="btn danger" style="width:100%">Open dispute and freeze funds</button></form>`);
    $("#df", m).onsubmit = async (e) => {
      e.preventDefault();
      try { await api(`/transactions/${t.id}/dispute`, { body: { reason: e.target.reason.value } }); closeModal(); toast("Dispute opened; funds frozen"); route(); }
      catch (err) { toast(err.message, true); }
    };
  }

  async function loadChat(t) {
    const { messages } = await api(`/transactions/${t.id}/messages`);
    const c = $("#chat");
    c.innerHTML = messages.length ? messages.map((m) => `<div class="msg ${m.sender_id === S.me.id ? "mine" : ""} ${m.flagged ? "flagged" : ""}"><span class="from">${esc(m.sender_name)} · ${when(m.created_at)}</span>${esc(m.body)}${m.flagged ? `<span class="flag">⚠ Flagged: ${esc(m.flag_reason)}. Payment outside Vouch isn't protected.</span>` : ""}</div>`).join("")
      : `<p class="small">No messages yet.</p>`;
    c.scrollTop = c.scrollHeight;
  }

  // ---------------------------------------------------------------- profile
  pages.user = async (id) => {
    const { user: u } = await api(`/users/${id}`);
    const r = u.reputation;
    view.innerHTML = `<div class="stack" style="max-width:640px">
      <h1>${esc(u.display_name)} ${badge(u.verified)}</h1>
      <p class="small">Member since ${when(u.member_since)}${u.verified ? ` · verified ${when(u.verified_at)}` : ""}</p>
      <div class="panel"><h3>Reputation</h3><p><strong>${r.completed_transactions}</strong> completed escrow deal${r.completed_transactions === 1 ? "" : "s"}${r.ratings ? ` · <strong>${r.avg_stars}★</strong> from ${r.ratings} rating${r.ratings === 1 ? "" : "s"}` : ""}</p>
      <p class="small">Every rating here came from a finished escrow transaction between two different verified people. There is no other way to get one.</p>
      ${u.recent_ratings.map((x) => `<p>${"★".repeat(x.stars)}<span style="color:var(--line)">${"★".repeat(5 - x.stars)}</span> ${esc(x.comment || "")} <span class="small">— ${esc(x.rater)}, ${when(x.created_at)}</span></p>`).join("")}</div></div>`;
  };

  // ---------------------------------------------------------------- admin
  pages.admin = async () => {
    if (!S.me || !S.me.is_admin) { view.innerHTML = `<div class="empty">Moderators only.</div>`; return; }
    const [{ disputes }, ov] = await Promise.all([api("/admin/disputes"), api("/admin/overview")]);
    const st = ov.transaction_states;
    view.innerHTML = `<h1>Disputes</h1>
      <div class="stats">
        <div><strong>${ov.verified_users}/${ov.users}</strong><span>users verified</span></div>
        <div><strong>${(st.funded || 0) + (st.handed_off || 0)}</strong><span>deals in escrow</span></div>
        <div><strong>${st.released || 0}</strong><span>released</span></div>
        <div><strong>${st.refunded || 0}</strong><span>refunded</span></div>
        <div><strong>${ov.flagged_messages}</strong><span>flagged messages</span></div>
        <div><button class="btn quiet sm" id="sweep">Run timers now</button></div>
      </div>
      ${disputes.length ? disputes.map(disputeCard).join("") : `<div class="empty">No open disputes.</div>`}`;
    $("#sweep").onclick = async () => { const r = await api("/admin/sweep", { body: {} }); toast(r.acted.length ? `Timers acted on ${r.acted.length} deal(s)` : "Nothing due"); route(); };
    disputes.forEach((d) => {
      $(`#rel-${d.id}`).onclick = () => resolve(d.id, "release");
      $(`#ref-${d.id}`).onclick = () => resolve(d.id, "refund");
    });
    async function resolve(id, outcome) {
      const note = $(`#note-${id}`).value;
      try { await api(`/admin/transactions/${id}/resolve`, { body: { outcome, note } }); toast(outcome === "release" ? "Released to seller" : "Refunded to buyer"); route(); }
      catch (e) { toast(e.message, true); }
    }
  };
  const disputeCard = (d) => `
    <div class="panel stack" style="margin-bottom:1.25rem">
      <div class="spread"><h2>#${d.id} · ${esc(d.listing.title)} · ${money(d.amount_cents)}</h2>${stateChip(d.state)}</div>
      <div class="notice red"><strong>${esc(d.dispute_opened_by === d.buyer_id ? d.buyer.display_name + " (buyer)" : d.seller.display_name + " (seller)")}:</strong> ${esc(d.dispute_reason)}</div>
      <div class="evidence">
        <div>${d.listing_photo ? `<img src="${d.listing_photo}" alt="Listing photo">` : `<div class="photo"></div>`}<p class="small">Listing photo, taken in-app</p></div>
        <div>
          <h3>Chat</h3><div class="chat">${d.messages.length ? d.messages.map((m) => `<div class="msg ${m.flagged ? "flagged" : ""}"><span class="from">${esc(m.sender_name)} · ${when(m.created_at)}</span>${esc(m.body)}${m.flagged ? `<span class="flag">⚠ ${esc(m.flag_reason)}</span>` : ""}</div>`).join("") : `<p class="small">No messages.</p>`}</div>
          <h3 style="margin-top:1rem">Timeline</h3><ul class="timeline">${d.events.map((e) => `<li class="${/failed|disputed/.test(e.kind) ? "warn" : ""}"><div>${esc(eventLabel(e.kind))}${e.detail ? ` — ${esc(e.detail)}` : ""}<time>${when(e.created_at)}</time></div></li>`).join("")}</ul>
        </div>
      </div>
      <div class="field"><label>Decision note (both parties see it)</label><input id="note-${d.id}" placeholder="e.g. Photo shows 6th edition; buyer's claim stands"></div>
      <div class="row"><button class="btn" id="rel-${d.id}">Release ${money(d.amount_cents)} to ${esc(d.seller.display_name)}</button><button class="btn danger" id="ref-${d.id}">Refund ${esc(d.buyer.display_name)}</button></div>
    </div>`;

  // ---------------------------------------------------------------- router
  async function route() {
    const hash = location.hash || "#/";
    const [path] = hash.slice(1).split("?");
    const parts = path.split("/").filter(Boolean);
    try { await refreshMe(); } catch (_) { renderHeader(); }
    view.innerHTML = `<p class="small">Loading…</p>`;
    try {
      if (parts.length === 0) return await pages.home();
      if (parts[0] === "listing" && parts[1]) return await pages.listing(parts[1]);
      if (parts[0] === "sell") return await pages.sell();
      if (parts[0] === "verify") return await pages.verify();
      if (parts[0] === "deals") return await pages.deals();
      if (parts[0] === "deal" && parts[1]) return await pages.deal(parts[1]);
      if (parts[0] === "user" && parts[1]) return await pages.user(parts[1]);
      if (parts[0] === "admin") return await pages.admin();
      view.innerHTML = `<div class="empty">That page doesn't exist. <a href="#/">Back to browsing.</a></div>`;
    } catch (e) {
      if (e.status === 401) { view.innerHTML = ""; authModal("login", route); }
      else view.innerHTML = `<div class="notice red">${esc(e.message)}</div>`;
    }
  }
  window.addEventListener("hashchange", route);
  refreshMe().then(route).catch((e) => { view.innerHTML = `<div class="notice red">${esc(e.message)}</div>`; });
})();
