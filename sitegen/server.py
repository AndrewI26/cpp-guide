"""Static file server for site/, optionally rebuilding on change.

It serves files only: practice code is compiled and run in the visitor's browser (see toolchain.py).
"""
from __future__ import annotations

import importlib
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from . import builder, md, render, content as content_mod

_build_lock = threading.Lock()


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(builder.SITE), **kwargs)

    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".wasm": "application/wasm",
                      ".hpp": "text/plain; charset=utf-8"}

    def end_headers(self):
        # The WASM compiler lives in a versioned directory, so it can be cached forever; everything
        # else is rebuilt on change and must not be cached.
        if self.path.split("?")[0].startswith("/static/vendor/"):
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        else:
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):  # only log errors
        if len(args) > 1 and str(args[1])[:1] in "45":
            super().log_message(fmt, *args)


def _watch(interval: float = 0.75) -> None:
    last = builder.snapshot()
    while True:
        time.sleep(interval)
        now = builder.snapshot()
        if now != last:
            last = now
            try:
                with _build_lock:
                    # pick up template / markdown changes without restarting
                    for mod in (md, content_mod, render, builder):
                        importlib.reload(mod)
                    builder.build(quiet=True)
                print(f"[{time.strftime('%H:%M:%S')}] rebuilt")
            except Exception as exc:  # keep serving the last good build
                print(f"[{time.strftime('%H:%M:%S')}] build failed: {exc!r}")


def _listen(host: str, port: int, handler) -> ThreadingHTTPServer:
    try:
        return ThreadingHTTPServer((host, port), handler)
    except OSError as exc:
        raise SystemExit(f"Can't listen on {host}:{port} ({exc.strerror}). Try another port: --port {port + 1}")


def serve(host: str = "127.0.0.1", port: int = 8000, watch: bool = True, build: bool = True) -> None:
    if build or not builder.SITE.exists():
        builder.build()
    if watch:
        threading.Thread(target=_watch, daemon=True).start()
    httpd = _listen(host, port, Handler)
    print(f"Serving on http://{host}:{port}  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
