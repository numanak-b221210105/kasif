// Page side of the in-browser Python server: routes the app's API calls to a Web Worker
// running the original FastAPI app, shows load progress, and adds soft UI sounds.
(() => {
  const S = document.currentScript;
  const ROUTES = (S.dataset.routes || "").split(",").map((s) => s.trim()).filter(Boolean);
  const BASE = new URL(".", location.href).pathname; // e.g. /mizan/
  const AUTO_SOUNDS = S.dataset.sounds !== "app";
  const tr = (navigator.language || "tr").toLowerCase().startsWith("tr");
  const L = (a, b) => (tr ? a : b);
  const worker = new Worker(new URL("py-worker.mjs", location.href), { type: "module" });
  const pending = new Map();
  let nextId = 1, isReady = false;

  // ---- loading card ----
  const card = document.createElement("div");
  card.className = "pyb-card";
  card.innerHTML = `<style>
.pyb-card{position:fixed;z-index:2147483000;right:16px;bottom:calc(16px + env(safe-area-inset-bottom));width:min(320px,calc(100vw - 32px));padding:12px 14px;border-radius:14px;
background:rgba(20,22,30,.92);color:#f4f1ea;font:500 13px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif;box-shadow:0 12px 40px -12px rgba(0,0,0,.5);backdrop-filter:blur(8px);transition:opacity .5s,transform .5s}
.pyb-card.done{opacity:0;transform:translateY(10px);pointer-events:none}
.pyb-card b{display:block;font-weight:650;margin-bottom:6px}.pyb-card small{display:block;opacity:.7;margin-top:6px;font-size:11.5px}
.pyb-bar{height:4px;border-radius:4px;background:rgba(255,255,255,.15);overflow:hidden}.pyb-bar i{display:block;height:100%;width:3%;background:linear-gradient(90deg,#7dd3fc,#a7f3d0);transition:width .3s}
.pyb-card.err{background:#5b1d1d}
</style><b>${L("Python tarayıcında hazırlanıyor…", "Starting Python in your browser…")}</b><div class="pyb-bar"><i></i></div><small>${L("İlk açılışta bir kez indirilir (~15 MB), sonra çevrimdışı da çalışır. Verilerin bu cihazdan çıkmaz.", "Downloaded once (~15 MB), then it also works offline. Your data never leaves this device.")}</small>`;
  const mount = () => document.body.append(card);
  document.body ? mount() : document.addEventListener("DOMContentLoaded", mount);
  const bar = card.querySelector("i");

  worker.onmessage = (e) => {
    const m = e.data;
    if (m.type === "progress") {
      bar.style.width = Math.round(m.pct * 100) + "%";
      if (m.step === "ready") { isReady = true; setTimeout(() => card.classList.add("done"), 400); setTimeout(() => card.remove(), 1200); window.dispatchEvent(new Event("pybridge:ready")); }
    } else if (m.type === "fatal") {
      card.classList.add("err");
      card.querySelector("b").textContent = L("Python başlatılamadı", "Python failed to start");
      card.querySelector("small").textContent = m.message + " · " + L("Sayfayı yenilemeyi dene.", "Try reloading the page.");
      for (const p of pending.values()) p.reject(new TypeError(m.message));
      pending.clear();
    } else if (m.type === "response") {
      const p = pending.get(m.id);
      if (!p) return;
      pending.delete(m.id);
      if (m.error) { p.resolve(new Response(JSON.stringify({ detail: m.error }), { status: 500, headers: { "content-type": "application/json" } })); return; }
      p.resolve(new Response(m.status === 204 || m.status === 304 ? null : m.body, { status: m.status, headers: m.headers }));
    }
  };

  // ---- fetch interception ----
  const realFetch = window.fetch.bind(window);
  function apiPath(url) {
    if (url.origin !== location.origin) return null;
    let p = url.pathname;
    if (p.startsWith(BASE) && BASE !== "/") p = "/" + p.slice(BASE.length);
    return ROUTES.some((r) => (r.endsWith("/") ? p.startsWith(r) : p === r || p.startsWith(r + "/") || p.startsWith(r + "?"))) ? p : null;
  }
  window.fetch = async (input, init) => {
    const req = new Request(input, init);
    const url = new URL(req.url);
    const path = apiPath(url);
    if (!path) return realFetch(input, init);
    const body = ["GET", "HEAD"].includes(req.method) ? null : await req.arrayBuffer();
    const headers = [...req.headers.entries()];
    const id = nextId++;
    const res = await new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject });
      worker.postMessage({ type: "request", id, method: req.method, path, query: url.search.slice(1), headers, body }, body ? [body] : []);
    });
    if (AUTO_SOUNDS && req.method !== "GET") sound(res.ok ? "ok" : "err");
    return res;
  };

  // ---- soft sounds ----
  let ctx = null;
  let on = true;
  try { on = localStorage.getItem("pyb.sound") !== "0"; } catch {}
  function tone(f, t0, dur, vol, type = "sine") {
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.type = type; o.frequency.value = f;
    g.gain.setValueAtTime(0.0001, ctx.currentTime + t0);
    g.gain.exponentialRampToValueAtTime(vol, ctx.currentTime + t0 + 0.01);
    g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + t0 + dur);
    o.connect(g).connect(ctx.destination);
    o.start(ctx.currentTime + t0); o.stop(ctx.currentTime + t0 + dur + 0.02);
  }
  let last = 0;
  function sound(kind) {
    if (!on) return;
    const now = performance.now();
    if (now - last < 120) return;
    last = now;
    try {
      ctx ??= new AudioContext();
      if (ctx.state !== "running") ctx.resume();
      if (kind === "ok") { tone(880, 0, 0.25, 0.035); tone(1320, 0.07, 0.3, 0.025); }
      else if (kind === "err") { tone(330, 0, 0.22, 0.04, "triangle"); tone(262, 0.1, 0.3, 0.035, "triangle"); }
      else if (kind === "click") tone(1000, 0, 0.05, 0.02);
    } catch {}
  }
  window.pybridge = {
    get ready() { return isReady; },
    sound,
    get soundOn() { return on; },
    setSound(v) { on = v; try { localStorage.setItem("pyb.sound", v ? "1" : "0"); } catch {} },
  };

  // A sound on/off button placed next to an existing header button of the app.
  function soundButton() {
    const anchor = S.dataset.soundBefore && document.querySelector(S.dataset.soundBefore);
    if (!anchor) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = anchor.className;
    btn.title = L("Sesler", "Sounds");
    const paint = () => {
      btn.setAttribute("aria-pressed", String(on));
      btn.setAttribute("aria-label", on ? L("Sesleri kapat", "Mute sounds") : L("Sesleri aç", "Unmute sounds"));
      btn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 9v6h4l5 4V5L8 9Z"/>${on ? '<path d="M16.5 8.5a5 5 0 0 1 0 7M19 6a8.5 8.5 0 0 1 0 12"/>' : '<path d="M16 9l6 6M22 9l-6 6"/>'}</svg>`;
    };
    btn.onclick = () => { window.pybridge.setSound(!on); paint(); sound("click"); };
    paint();
    anchor.before(btn);
  }
  document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", soundButton) : soundButton();

  if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
})();
