"""Sunucuyu başlatır ve hazır olduğunda tarayıcıyı açar.

Kullanım: python -m kasif.serve [--port 8200] [--no-browser]
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def _find_free_port(host: str, start: int, attempts: int = 20) -> int | None:
    for port in range(start, start + attempts):
        if not _port_in_use(host, port):
            return port
    return None


def _open_when_ready(url: str, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=1):
                webbrowser.open(url)
                return
        except OSError:
            time.sleep(0.4)
    print(f"Sunucu {timeout:.0f} sn içinde yanıt vermedi; yukarıdaki hata mesajına bakın.")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="kasif-serve")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8200)
    p.add_argument("--no-browser", action="store_true")
    args = p.parse_args(argv)

    port = _find_free_port(args.host, args.port)
    if port is None:
        print(f"{args.port}-{args.port + 19} arasındaki tüm portlar dolu. Örnek: --port 9000")
        return 1
    if port != args.port:
        print(f"{args.port} portu başka bir program tarafından kullanılıyor, {port} kullanılacak.")
    url = f"http://{args.host}:{port}"

    import uvicorn

    env_file = Path(".env")
    print(f"\n  Kâşif başlatılıyor: {url}\n  Durdurmak için: Ctrl+C\n")
    if not args.no_browser:
        threading.Thread(target=_open_when_ready, args=(url,), daemon=True).start()
    uvicorn.run(
        "kasif.api:app",
        host=args.host,
        port=port,
        env_file=str(env_file) if env_file.exists() else None,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
