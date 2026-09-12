/* Vouch frontend. Vanilla JS, talks to the Flask API in app.py. */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const money = n => "$" + Number(n).toFixed(2);

let CONFIG = null;
let ME = null;      // {user, balance, reputation}
let shotData = null; // base64 jpeg of the listing photo

// ------------------------------------------------------------------ api
async function api(path, method = "GET", body) {
  const r = await fetch("/api" + path, {
    method, headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

function toast(msg, ms = 2800) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(t._t); t._t = setTimeout(() => (t.hidden = true), ms);
}
function showMsg(el, text, ok) {
  el.textContent = text; el.className = "msg " + (ok ? "ok" : "no"); el.hidden = false;
}

// -------------------------------------------------------------- session
async function loadMe() {
  ME = await api("/me");
  renderWho();
  return ME;
}

function renderWho() {
  const who = $("#who"), nav = $("#nav");
  if (!ME?.user) { who.innerHTML = ""; nav.hidden = true; return; }
  const u = ME.user;
  nav.hidden = false;
  $("#nav-admin").hidden = !u.is_admin;
  who.innerHTML = `
    <span>${u.name}</span>
    ${u.verified ? `<span class="badge ok">Verified human</span>` : `<span class="badge no">Not verified</span>`}
    ${ME.balance != null ? `<span class="bal">wallet ${money(ME.balance)}</span>` : ""}
    ${ME.reputation?.count ? `<span class="fine">${ME.reputation.avg}★ · ${ME.reputation.count} deals</span>` : ""}
    <button class="link" id="btn-out">Sign out</button>`;
  $("#btn-out").onclick = async () => { await api("/logout", "POST"); ME = null; renderWho(); go("signin"); };
}

// -------------------------------------------------------------- routing
const VIEWS = ["signin", "verify", "browse", "sell", "deals", "admin"];
function go(view) {
  location.hash = view;
}
async function route() {
  let view = (location.hash || "#browse").slice(1);
  if (!VIEWS.includes(view)) view = "browse";
  if (!ME?.user && view !== "signin") view = "signin";
  if (ME?.user && !ME.user.verified && (view === "sell" || view === "deals")) view = "verify";
  if (view === "admin" && !ME?.user?.is_admin) view = "browse";

  VIEWS.forEach(v => ($("#view-" + v).hidden = v !== view));
  $$("#nav a").forEach(a => a.classList.toggle("active", a.getAttribute("href") === "#" + view));

  if (view === "verify") renderVerify();
  if (view === "browse") renderListings();
  if (view === "sell") await prepSell();
  if (view === "deals") renderDeals();
  if (view === "admin") renderAdmin();
}
window.addEventListener("hashchange", route);

// -------------------------------------------------------------- sign in
$("#signin-form").addEventListener("submit", async e => {
  e.preventDefault();
  const f = new FormData(e.target);
  try {
    const { user, returning } = await api("/signup", "POST", { name: f.get("name"), email: f.get("email") });
    await loadMe();
    toast(returning ? `Welcome back, ${user.name}` : `Account created`);
    go(user.verified ? "browse" : "verify");
  } catch (err) { toast(err.message); }
});

// --------------------------------------------------------- verification
function renderVerify() {
  const mock = CONFIG.persona.mock;
  $("#verify-mock").hidden = !mock;
  $("#verify-live").hidden = mock;
  $("#verify-msg").hidden = true;
}

async function submitVerification(inquiry_id, status) {
  try {
    const res = await api("/verify", "POST", { inquiry_id, status });
    await loadMe();
    if (res.verified) {
      showMsg($("#verify-msg"), `Verified. A wallet with ${money(res.balance)} was opened for you.`, true);
      setTimeout(() => go("browse"), 900);
    } else {
      showMsg($("#verify-msg"), res.message, false);
    }
  } catch (err) { showMsg($("#verify-msg"), err.message, false); }
}

$$("#verify-mock button").forEach(b =>
  b.addEventListener("click", () => submitVerification("mock-" + Date.now(), b.dataset.outcome)));

$("#btn-persona").addEventListener("click", () => {
  if (!window.Persona) return toast("Persona script didn't load");
  const client = new Persona.Client({
    templateId: CONFIG.persona.template_id,
    environmentId: CONFIG.persona.environment_id,
    referenceId: ME.user.id,
    onComplete: ({ inquiryId, status }) => submitVerification(inquiryId, status),
    onCancel: () => toast("Verification cancelled"),
    onError: e => toast("Persona error: " + (e?.message || "unknown")),
  });
  client.open();
});

$("#btn-browse-only").addEventListener("click", () => go("browse"));

// --------------------------------------------------------------- browse
async function renderListings() {
  const wrap = $("#listings");
  const items = await api("/listings");
  if (!items.length) {
    wrap.innerHTML = `<p class="empty">Nothing listed yet. Be the first — every listing here comes from a verified seller.</p>`;
    return;
  }
  wrap.innerHTML = items.map(l => `
    <article class="listing card">
      <img src="${l.photo}" alt="${l.title}">
      <div class="body">
        <span class="price">${money(l.price)}</span>
        <h3>${l.title}</h3>
        <p class="fine">${l.description || ""}</p>
        <span class="seller"><span class="dot"></span>${l.seller_name}${l.seller_rep.count ? ` · ${l.seller_rep.avg}★ (${l.seller_rep.count})` : " · new seller"}</span>
        <span class="fine">Proof word in photo: ${l.proof_word}</span>
        ${l.seller_id === ME.user.id
          ? `<button class="ghost" disabled>Your listing</button>`
          : `<button class="primary" data-buy="${l.id}" data-price="${l.price}">Buy — ${money(l.price)} held in escrow</button>`}
      </div>
    </article>`).join("");

  $$("[data-buy]", wrap).forEach(b => b.addEventListener("click", async () => {
    if (!ME.user.verified) return go("verify");
    b.disabled = true;
    try {
      await api("/deals", "POST", { listing_id: b.dataset.buy });
      await loadMe();
      toast(`${money(b.dataset.price)} moved out of your wallet and into escrow`);
      go("deals");
    } catch (err) { toast(err.message); b.disabled = false; }
  }));
}

// ----------------------------------------------------------------- sell
let stream = null;
async function prepSell() {
  shotData = null;
  $("#shot-preview").hidden = true; $("#cam").hidden = false;
  $("#btn-retake").hidden = true; $("#btn-snap").disabled = true; $("#btn-list").disabled = true;
  try {
    const { proof_word } = await api("/proof-word");
    $("#proof-word").textContent = proof_word;
  } catch (err) { $("#proof-word").textContent = "—"; toast(err.message); }
}

$("#btn-cam").addEventListener("click", async () => {
  try {
    stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false });
    $("#cam").srcObject = stream;
    $("#btn-snap").disabled = false;
  } catch (e) {
    // No camera (e.g. demo laptop without webcam): render a stand-in frame so the flow still works.
    toast("No camera available — using a placeholder frame for the demo");
    drawPlaceholder();
    $("#btn-snap").disabled = false;
  }
});

function drawPlaceholder() {
  const c = $("#shot"); c.width = 640; c.height = 480;
  const g = c.getContext("2d");
  g.fillStyle = "#e7ebef"; g.fillRect(0, 0, 640, 480);
  g.fillStyle = "#fff"; g.fillRect(200, 300, 240, 120);
  g.fillStyle = "#1c2230"; g.font = "bold 34px sans-serif"; g.textAlign = "center";
  g.fillText($("#proof-word").textContent, 320, 372);
  g.font = "18px sans-serif"; g.fillStyle = "#5f6672";
  g.fillText("camera unavailable — placeholder", 320, 60);
  c.hidden = false; $("#cam").hidden = true;
}

$("#btn-snap").addEventListener("click", () => {
  const c = $("#shot");
  if (stream) {
    const v = $("#cam"); c.width = v.videoWidth || 640; c.height = v.videoHeight || 480;
    c.getContext("2d").drawImage(v, 0, 0, c.width, c.height);
    stream.getTracks().forEach(t => t.stop()); stream = null;
  }
  shotData = c.toDataURL("image/jpeg", 0.85);
  $("#shot-preview").src = shotData; $("#shot-preview").hidden = false;
  c.hidden = true; $("#cam").hidden = true;
  $("#btn-snap").disabled = true; $("#btn-retake").hidden = false; $("#btn-list").disabled = false;
});

$("#btn-retake").addEventListener("click", prepSell);

$("#btn-list").addEventListener("click", async () => {
  const msg = $("#sell-msg"); msg.hidden = true;
  try {
    await api("/listings", "POST", {
      title: $("#sell-title").value, price: $("#sell-price").value,
      category: $("#sell-cat").value, description: $("#sell-desc").value, photo: shotData,
    });
    $("#sell-title").value = ""; $("#sell-price").value = ""; $("#sell-desc").value = "";
    toast("Listed. Buyers will see your verified status and the proof word.");
    go("browse");
  } catch (err) { showMsg(msg, err.message, false); }
});

// ---------------------------------------------------------------- deals
const STATE_LABEL = {
  funded: "Paid — held in escrow", handed_off: "Handed off — awaiting confirmation",
  released: "Complete", disputed: "Disputed — funds frozen", refunded: "Refunded",
};

function fmtTime(t) { return new Date(t * 1000).toLocaleString([], { dateStyle: "short", timeStyle: "short" }); }

function renderDealCard(d, isAdmin = false) {
  const node = $("#tpl-deal").content.firstElementChild.cloneNode(true);
  $(".thumb", node).src = d.listing?.photo || "";
  $(".deal-title", node).textContent = `${d.listing?.title || "Listing"} — ${money(d.amount)}`;
  $(".deal-parties", node).textContent = `${d.buyer.name} (buyer) → ${d.seller.name} (seller)`;
  const st = $(".state", node); st.textContent = STATE_LABEL[d.state]; st.dataset.s = d.state;

  // Ledger: light up whichever slot the money is in right now.
  const slot = { funded: "escrow", handed_off: "escrow", disputed: "escrow", released: "seller", refunded: "buyer" }[d.state];
  $$(".slot", node).forEach(s => {
    s.classList.toggle("active", s.dataset.slot === slot);
    s.classList.toggle("frozen", d.state === "disputed" && s.dataset.slot === "escrow");
    $(".slot-amt", s).textContent = s.dataset.slot === slot ? money(d.amount) : "";
  });
  const tracks = $$(".track", node);
  tracks[0].classList.toggle("done", slot !== "buyer");
  tracks[1].classList.toggle("done", slot === "seller");

  const body = $(".deal-body", node);
  const btn = (label, cls, fn) => { const b = document.createElement("button"); b.className = cls; b.textContent = label; b.onclick = fn; return b; };
  const act = async (fn, okMsg) => { try { await fn(); await loadMe(); toast(okMsg); route(); } catch (e) { toast(e.message); } };

  if (d.state === "funded" && d.role === "buyer") {
    body.innerHTML = `<p>Your money is with us, not the seller. Meet up, check the item, then show the seller this code. They type it in to prove you both were there.</p>
      <div class="code">${d.handoff_code}</div>
      <p class="fine">Don't share this code before you've seen the item in person.</p>`;
    body.append(btn("Something's wrong — dispute", "danger", () => disputeFlow(d)));
  }
  if (d.state === "funded" && d.role === "seller") {
    body.innerHTML = `<p>${money(d.amount)} is confirmed and held in escrow — you'll never be asked to trust a payment screenshot. Meet the buyer, hand over the item, and enter the code they show you.</p>`;
    const row = document.createElement("div"); row.className = "row";
    const inp = document.createElement("input"); inp.placeholder = "6-digit code"; inp.inputMode = "numeric"; inp.maxLength = 6;
    row.append(inp, btn("Confirm handoff", "primary", () => act(() => api(`/deals/${d.id}/handoff`, "POST", { code: inp.value }), "Handoff confirmed. Buyer has 24h to confirm or dispute.")));
    body.append(row);
  }
  if (d.state === "handed_off" && d.role === "buyer") {
    body.innerHTML = `<p>Seller says the handoff happened. If the item is as described, release the money. If not, dispute — the money stays frozen.</p>
      <p class="deadline">Auto-releases ${fmtTime(d.confirm_deadline)} if you do nothing.</p>`;
    const row = document.createElement("div"); row.className = "row";
    row.append(btn("Item is as described — release payment", "primary", () => act(() => api(`/deals/${d.id}/confirm`, "POST"), `${money(d.amount)} released to ${d.seller.name}`)),
               btn("Dispute", "danger", () => disputeFlow(d)));
    body.append(row);
  }
  if (d.state === "handed_off" && d.role === "seller") {
    body.innerHTML = `<p>Waiting on the buyer. If they do nothing, ${money(d.amount)} auto-releases to you ${fmtTime(d.confirm_deadline)}.</p>`;
  }
  if (d.state === "disputed") {
    body.innerHTML = `<p>Disputed by ${d.dispute_by === d.buyer_id ? d.buyer.name : d.seller.name}: “${d.dispute_reason}”. Funds are frozen until a reviewer decides.</p>`;
    if (isAdmin) {
      const row = document.createElement("div"); row.className = "row";
      const ban = document.createElement("label"); ban.innerHTML = `<input type="checkbox"> ban the losing party`;
      const banned = () => $("input", ban).checked;
      row.append(btn("Rule for buyer (refund)", "ghost", () => act(() => api(`/admin/deals/${d.id}/resolve`, "POST", { winner: "buyer", ban: banned() }), "Refunded to buyer")),
                 btn("Rule for seller (release)", "ghost", () => act(() => api(`/admin/deals/${d.id}/resolve`, "POST", { winner: "seller", ban: banned() }), "Released to seller")),
                 ban);
      body.append(row);
    }
  }
  if (d.state === "released" && d.role === "buyer" && d.reviewed) {
    body.innerHTML = `<p>Done. Thanks for rating ${d.seller.name}.</p>`;
  }
  if (d.state === "released" && d.role === "buyer" && !d.reviewed) {
    body.innerHTML = `<p>Done. Rate ${d.seller.name} — this rating is tied to a completed deal between two verified people, so it can't be faked.</p>`;
    const row = document.createElement("div"); row.className = "row";
    [1, 2, 3, 4, 5].forEach(n => row.append(btn(`${n}★`, "ghost", () => act(() => api(`/deals/${d.id}/review`, "POST", { rating: n }), "Thanks — rating recorded"))));
    body.append(row);
  }
  if (d.state === "released" && d.role === "seller") body.innerHTML = `<p>${money(d.amount)} is in your wallet.</p>`;
  if (d.state === "refunded") body.innerHTML = `<p>${money(d.amount)} went back to ${d.buyer.name}. The listing is open again.</p>`;

  $(".events", node).innerHTML = d.events.map(e =>
    `<li><b>${e.kind.replace("_", " ")}</b> — ${e.detail || ""} <span class="fine">(${fmtTime(e.at)})</span></li>`).join("");
  return node;
}

async function disputeFlow(d) {
  const reason = prompt("What went wrong? This is shown to the reviewer.");
  if (reason === null) return;
  try { await api(`/deals/${d.id}/dispute`, "POST", { reason }); toast("Disputed — funds frozen"); route(); }
  catch (e) { toast(e.message); }
}

async function renderDeals() {
  const deals = await api("/deals");
  const open = deals.filter(d => ["funded", "handed_off", "disputed"].includes(d.state)).length;
  $("#deal-count").textContent = open; $("#deal-count").hidden = !open;
  const wrap = $("#deals"); wrap.innerHTML = "";
  if (!deals.length) { wrap.innerHTML = `<p class="empty">No deals yet. Buy something and you'll see the money move here.</p>`; return; }
  deals.forEach(d => wrap.append(renderDealCard(d)));
}

async function renderAdmin() {
  const deals = await api("/deals?all=1");
  const wrap = $("#admin-deals"); wrap.innerHTML = "";
  const disputed = deals.filter(d => d.state === "disputed");
  if (!disputed.length) wrap.innerHTML = `<p class="empty">No open disputes.</p>`;
  disputed.forEach(d => wrap.append(renderDealCard(d, true)));
  const others = deals.filter(d => d.state !== "disputed");
  if (others.length) {
    const h = document.createElement("h3"); h.textContent = "All other deals"; wrap.append(h);
    others.forEach(d => wrap.append(renderDealCard(d, true)));
  }
}
$("#btn-ff").addEventListener("click", async () => {
  try { await api("/dev/fast-forward", "POST"); toast("Time advanced — expired windows settled"); route(); }
  catch (e) { toast(e.message); }
});

// ----------------------------------------------------------------- boot
(async () => {
  CONFIG = await api("/config");
  try { await loadMe(); } catch { ME = null; }
  route();
})();
