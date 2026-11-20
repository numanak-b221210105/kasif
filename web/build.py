"""Builds the browser version of this project into ``site/``.

The original FastAPI app runs unchanged inside the browser with Pyodide (CPython
compiled to WebAssembly). Everything is served from the same site: the Python
runtime, the wheels and the app itself, so there is no server and no CDN.

    python web/build.py            # then serve site/ with any static server
    python -m http.server -d site  # e.g.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

WEB = Path(__file__).resolve().parent
ROOT = WEB.parent
OUT = ROOT / "site"
CFG = json.loads((WEB / "config.json").read_text(encoding="utf-8"))

PYODIDE = CFG.get("pyodide", "314.0.8")
PLATFORM = "pyemscripten_2026_0_wasm32"
PY = "3.14"
RUNTIME = ["pyodide.mjs", "pyodide.asm.mjs", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json"]


def log(msg: str) -> None:
    print(f"  {msg}", flush=True)


def fetch_runtime(dest: Path) -> None:
    cache = ROOT / ".pyodide-cache" / PYODIDE
    if not all((cache / f).exists() for f in RUNTIME):
        url = f"https://registry.npmjs.org/pyodide/-/pyodide-{PYODIDE}.tgz"
        log(f"downloading Pyodide {PYODIDE}")
        data = urllib.request.urlopen(url, timeout=120).read()
        cache.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            for f in RUNTIME:
                (cache / f).write_bytes(tar.extractfile(f"package/{f}").read())  # type: ignore[union-attr]
    dest.mkdir(parents=True)
    for f in RUNTIME:
        shutil.copy2(cache / f, dest / f)


def fetch_wheels(dest: Path) -> list[str]:
    log("downloading wheels for WebAssembly")
    subprocess.run(
        [sys.executable, "-m", "pip", "download", "-q", "-d", str(dest), "--only-binary=:all:",
         "--platform", PLATFORM, "--python-version", PY, "--implementation", "cp",
         "--abi", "cp" + PY.replace(".", ""), "--abi", "none", *CFG["requirements"]],
        check=True,
    )
    skip = [s.lower() for s in CFG.get("skip_wheels", [])]
    names = []
    for w in sorted(dest.glob("*.whl")):
        if any(w.name.lower().startswith(s) for s in skip):
            w.unlink()
        else:
            names.append(w.name)
    return names


def build_app_wheel(dest: Path) -> str:
    """Packs the project's own package as a wheel (pure Python, so a zip is enough)."""
    pkg = ROOT / "src" / CFG["package"]
    version = re.search(r'__version__\s*=\s*"([^"]+)"', (pkg / "__init__.py").read_text(encoding="utf-8"))
    ver = version.group(1) if version else "0.0.0"
    name = f"{CFG['package']}-{ver}-py3-none-any.whl"
    info = f"{CFG['package']}-{ver}.dist-info"
    with zipfile.ZipFile(dest / name, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(pkg.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts:
                z.write(f, f.relative_to(pkg.parent).as_posix())
        z.writestr(f"{info}/METADATA", f"Metadata-Version: 2.1\nName: {CFG['package']}\nVersion: {ver}\n")
        z.writestr(f"{info}/WHEEL", "Wheel-Version: 1.0\nGenerator: web/build.py\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        z.writestr(f"{info}/RECORD", "")
    return name


def page(index: Path) -> str:
    html = index.read_text(encoding="utf-8")
    head = (
        f'<link rel="manifest" href="manifest.webmanifest">\n'
        f'<meta name="theme-color" content="{CFG["theme_color"]}">\n'
        f'<link rel="apple-touch-icon" href="icons/apple-touch-icon.png">\n'
        f'<meta name="apple-mobile-web-app-capable" content="yes">\n'
        f'<meta name="mobile-web-app-capable" content="yes">\n'
        f'<meta name="apple-mobile-web-app-title" content="{CFG["short_name"]}">\n'
        f'<script src="appkit.js"></script>\n'
        f'<script src="pybridge.js" data-app="{CFG["short_name"]}" data-accent="{CFG["theme_color"]}" data-routes="{",".join(CFG["routes"])}" data-sounds="{CFG.get("sounds", "auto")}" data-sound-before="{CFG.get("sound_button_before", "")}"></script>\n'
    )
    if not re.search(r'<meta[^>]+name="viewport"', html):
        head = '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n' + head
    i = html.lower().find("</title>")
    i = html.lower().find("<head>") + len("<head>") if i < 0 else i + len("</title>")
    return html[:i] + "\n" + head + html[i:]


def main() -> None:
    print(f"Building the browser version of {CFG['name']} -> {OUT.relative_to(ROOT)}/")
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir()
    fetch_runtime(OUT / "pyodide")
    wheels_dir = OUT / "wheels"
    wheels = fetch_wheels(wheels_dir)
    app = build_app_wheel(wheels_dir)
    files = []
    for i, (src, target) in enumerate(CFG.get("files", {}).items()):
        # Extra folders the app reads at runtime (e.g. sample documents), unpacked to `target`.
        name = f"files-{i}.zip"
        with zipfile.ZipFile(wheels_dir / name, "w", zipfile.ZIP_DEFLATED) as z:
            base = ROOT / src
            for f in sorted(base.rglob("*")):
                if f.is_file():
                    z.write(f, f.relative_to(base).as_posix())
        files.append({"zip": name, "dir": target})
    (OUT / "pybridge.json").write_text(json.dumps({
        "module": CFG["module"], "wheels": wheels, "app": app, "files": files,
        "env": CFG.get("env", {}), "persist": CFG.get("persist"),
    }, indent=1), encoding="utf-8")
    index = ROOT / CFG["index"]
    (OUT / "index.html").write_text(page(index), encoding="utf-8")
    for extra in CFG.get("copy", []):  # e.g. sample files the UI links to
        src = ROOT / extra
        (OUT / extra).parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, OUT / extra) if src.is_dir() else shutil.copy2(src, OUT / extra)
    for f in ["appkit.js", "pybridge.js", "py-worker.mjs"]:
        shutil.copy2(WEB / f, OUT / f)
    shutil.copytree(WEB / "icons", OUT / "icons")
    digest = hashlib.sha256(b"".join((OUT / "wheels" / w).read_bytes()[:4096] for w in [*wheels, app]) + (OUT / "index.html").read_bytes()).hexdigest()[:10]
    (OUT / "sw.js").write_text((WEB / "sw.js").read_text(encoding="utf-8").replace("__VERSION__", digest), encoding="utf-8")
    (OUT / "manifest.webmanifest").write_text(json.dumps({
        "name": CFG["name"], "short_name": CFG["short_name"], "description": CFG["description"],
        "id": "./", "start_url": "./", "scope": "./", "display": "standalone",
        "background_color": CFG["background_color"], "theme_color": CFG["theme_color"],
        "icons": [
            {"src": "icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
            {"src": "icons/maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / ".nojekyll").write_text("")
    size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file()) / 1e6
    log(f"done: {len(wheels) + 1} wheels, {size:.1f} MB")


if __name__ == "__main__":
    main()
