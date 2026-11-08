"""HTTP servers.

* serve  - serves site/ (optionally rebuilding on change) and handles POST /api/run, either by
           compiling locally or by forwarding to a separate runner (--runner-url).
* runner - only handles POST /api/run; meant to run in an isolated container with no network.
"""
from __future__ import annotations

import importlib
import json
import threading
import os
import time
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from . import builder, judge, md, render, content as content_mod
from .content import find_problem

_build_lock = threading.Lock()
RUNNER_URL: str | None = None     # set by serve(); when set, /api/run is forwarded there
RUNNER_ENABLED = True
MAX_BODY = 256_000


def _read_run_request(handler) -> tuple[int, dict]:
    length = int(handler.headers.get("Content-Length") or 0)
    if length > MAX_BODY:
        return 413, {"error": "submission too large"}
    try:
        req = json.loads(handler.rfile.read(length))
        slug, code = str(req["slug"]), str(req.get("code", ""))
    except (ValueError, KeyError, TypeError):
        return 400, {"error": "bad request"}
    problem = find_problem(slug) if slug.replace("-", "").isalnum() else None
    if problem is None:
        return 404, {"error": "unknown problem"}
    return 200, judge.run(code, problem.tests)


def _send_json(handler, code: int, payload: dict) -> None:
    data = json.dumps(payload).encode()
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(builder.SITE), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):  # only log API calls and errors
        if self.command == "POST" or (len(args) > 1 and str(args[1])[:1] in "45"):
            super().log_message(fmt, *args)

    def do_POST(self):
        if self.path != "/api/run":
            return _send_json(self, 404, {"error": "not found"})
        if not RUNNER_ENABLED:
            return _send_json(self, 503, {"error": "the code runner is disabled on this server"})
        if RUNNER_URL:
            return self._forward()
        _send_json(self, *_read_run_request(self))

    def _forward(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return _send_json(self, 413, {"error": "submission too large"})
        req = urllib.request.Request(RUNNER_URL.rstrip("/") + "/api/run", data=self.rfile.read(length),
                                     headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                _send_json(self, resp.status, json.loads(resp.read()))
        except urllib.error.HTTPError as exc:
            _send_json(self, exc.code, {"error": f"runner returned {exc.code}"})
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            _send_json(self, 502, {"error": f"runner unavailable: {exc}"})


class RunnerHandler(SimpleHTTPRequestHandler):
    """Only POST /api/run (and GET /healthz). Serves no files."""

    def log_message(self, fmt, *args):
        super().log_message(fmt, *args)

    def do_GET(self):
        if self.path == "/healthz":
            return _send_json(self, 200, {"ok": True, "compiler": judge.compiler_available()})
        _send_json(self, 404, {"error": "not found"})

    do_HEAD = do_GET

    def do_POST(self):
        if self.path != "/api/run":
            return _send_json(self, 404, {"error": "not found"})
        _send_json(self, *_read_run_request(self))


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


def serve(host: str = "127.0.0.1", port: int = 8000, watch: bool = True, build: bool = True,
          runner_url: str | None = None, runner: bool = True) -> None:
    global RUNNER_URL, RUNNER_ENABLED
    RUNNER_URL = runner_url or os.environ.get("CPPGUIDE_RUNNER_URL") or None
    RUNNER_ENABLED = runner
    if build or not builder.SITE.exists():
        builder.build()
    if RUNNER_URL:
        print(f"Forwarding /api/run to {RUNNER_URL}")
    elif runner and not judge.compiler_available():
        print(f"warning: '{judge.CXX}' not found, so 'Run tests' won't work. Install g++ or set CXX.")
    if watch:
        threading.Thread(target=_watch, daemon=True).start()
    httpd = _listen(host, port, Handler)
    print(f"Serving on http://{host}:{port}  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


def run_runner(host: str = "0.0.0.0", port: int = 9000) -> None:
    httpd = _listen(host, port, RunnerHandler)
    print(f"Runner listening on http://{host}:{port}  (max {judge.MAX_JOBS} concurrent jobs)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
