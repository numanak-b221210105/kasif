// Runs the project's own Python web app (ASGI) inside the browser with Pyodide.
// Wheels and the Python runtime are served from this site, so it works offline once cached.
import { loadPyodide } from "./pyodide/pyodide.mjs";

let ready;
let saving = Promise.resolve(); // IndexedDB writes, one at a time
const post = (m) => self.postMessage(m);

async function boot() {
  const cfg = await (await fetch("./pybridge.json")).json();
  post({ type: "progress", step: "runtime", pct: 0.05 });
  const py = await loadPyodide({ indexURL: new URL("./pyodide/", self.location.href).href, env: { HOME: "/home/pyodide", ...cfg.env } });
  const files = [...cfg.wheels, cfg.app];
  let done = 0;
  // Download in parallel, unpack in order.
  const bufs = await Promise.all(files.map(async (f) => {
    const r = await fetch("./wheels/" + f);
    if (!r.ok) throw new Error(`${f}: ${r.status}`);
    const b = await r.arrayBuffer();
    post({ type: "progress", step: "packages", pct: 0.35 + 0.45 * (++done / files.length) });
    return b;
  }));
  const site = py.runPython("import site; site.getsitepackages()[0]");
  for (let i = 0; i < files.length; i++) py.unpackArchive(bufs[i], "wheel", { extractDir: site });
  for (const f of cfg.files || []) {
    const r = await fetch("./wheels/" + f.zip);
    py.FS.mkdirTree(f.dir);
    py.unpackArchive(await r.arrayBuffer(), "zip", { extractDir: f.dir });
  }
  if (cfg.persist) {
    py.FS.mkdirTree(cfg.persist);
    py.FS.mount(py.FS.filesystems.IDBFS, {}, cfg.persist);
    await new Promise((res, rej) => py.FS.syncfs(true, (e) => (e ? rej(e) : res())));
  }
  post({ type: "progress", step: "app", pct: 0.85 });
  await py.runPythonAsync(BOOT_PY);
  const handle = py.globals.get("pybridge_handle");
  await py.runPythonAsync(`pybridge_load(${JSON.stringify(cfg.module)})`);
  post({ type: "progress", step: "ready", pct: 1 });
  return { py, handle, cfg };
}

const BOOT_PY = `
import sys, types, asyncio, importlib

# There are no threads in the browser: run "threadpool" work inline.
import anyio.to_thread
async def _run_sync(func, *args, abandon_on_cancel=False, cancellable=None, limiter=None):
    return func(*args)
anyio.to_thread.run_sync = _run_sync

# No sockets either: an httpx stand-in that reports "not reachable" so LLM features
# fall back cleanly to the rule-based path.
if "httpx" not in sys.modules:
    try:
        import httpx  # noqa
    except ImportError:
        m = types.ModuleType("httpx")
        class HTTPError(Exception): pass
        class Timeout:
            def __init__(self, *a, **k): pass
        def _offline(*a, **k): raise HTTPError("network calls are not available in the browser build")
        m.HTTPError = HTTPError; m.ConnectError = HTTPError; m.Timeout = Timeout
        m.get = m.post = m.request = _offline
        sys.modules["httpx"] = m

APP = None
def pybridge_load(spec):
    global APP
    mod, _, attr = spec.partition(":")
    APP = getattr(importlib.import_module(mod), attr or "app")

async def pybridge_handle(method, path, query, headers, body):
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "scheme": "https", "path": path, "raw_path": path.encode(),
        "query_string": query.encode(), "root_path": "",
        "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers],
        "client": ("127.0.0.1", 0), "server": ("localhost", 443),
    }
    data = body.to_bytes()
    sent = False
    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": data, "more_body": False}
        await asyncio.Event().wait()
    out = {"status": 500, "headers": [], "body": bytearray()}
    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
            out["headers"] = [(k.decode("latin-1"), v.decode("latin-1")) for k, v in msg.get("headers", [])]
        elif msg["type"] == "http.response.body":
            out["body"] += msg.get("body", b"")
    await APP(scope, receive, send)
    return out["status"], out["headers"], bytes(out["body"])
`;

self.onmessage = async (e) => {
  const m = e.data;
  if (m.type !== "request") return;
  try {
    ready ??= boot();
    const { py, handle, cfg } = await ready;
    const body = new Uint8Array(m.body || new ArrayBuffer(0));
    const res = await handle(m.method, m.path, m.query, py.toPy(m.headers), body);
    const [status, headers, bytes] = res.toJs();
    res.destroy?.();
    const buf = bytes.slice().buffer;
    post({ type: "response", id: m.id, status, headers, body: buf }, [buf]);
    if (cfg.persist && m.method !== "GET") saving = saving.then(() => new Promise((r) => py.FS.syncfs(false, () => r())));
  } catch (err) {
    post({ type: "response", id: m.id, status: 599, headers: [["content-type", "application/json"]], error: String(err?.message || err) });
  }
};
// Start loading right away so the first request is fast.
self.addEventListener("message", () => {}, { once: true });
ready = boot().catch((err) => { post({ type: "fatal", message: String(err?.message || err) }); throw err; });
