/* Shared helpers for host + guest pages. */
window.Pulse = (() => {
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (m) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[m]));
  const code = () => location.pathname.split("/").filter(Boolean).pop().toUpperCase();
  const mmss = (ms) => { const s = Math.max(0, Math.floor(ms / 1000)); return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; };
  async function api(path, opts = {}) {
    const r = await fetch(`/api/parties/${code()}${path}`, {
      method: opts.method || (opts.body ? "POST" : "GET"),
      headers: { "Content-Type": "application/json" },
      body: opts.body ? JSON.stringify(opts.body) : undefined, credentials: "same-origin",
    });
    let d = {}; try { d = await r.json(); } catch (_) {}
    if (!r.ok) { const e = new Error(d.error || `Request failed (${r.status})`); e.status = r.status; e.code = d.code; throw e; }
    return d;
  }
  let tt;
  function toast(msg, err = false) {
    let t = document.getElementById("toast");
    if (!t) { t = document.createElement("div"); t.id = "toast"; t.className = "toast"; document.body.appendChild(t); }
    t.textContent = msg; t.className = "toast show" + (err ? " err" : "");
    clearTimeout(tt); tt = setTimeout(() => (t.className = "toast"), err ? 4000 : 2200);
  }
  const art = (t, cls = "") => t.art_url ? `<img class="${cls}" src="${esc(t.art_url)}" alt="">` : `<div class="ph ${cls}" style="display:grid;place-items:center;font-weight:800;color:var(--muted)">${esc((t.title || "?")[0])}</div>`;
  return { esc, code, mmss, api, toast, art };
})();
