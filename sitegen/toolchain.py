"""The in-browser C++ toolchain: YoWASP Clang/LLD compiled to WebAssembly.

The npm tarball is pinned and integrity-checked, downloaded once into vendor/, and linked into
site/static/vendor/ at build time. Practice pages load it lazily in a Web Worker the first time
someone presses "Run tests", so the site can compile and run C++ with no server.
"""
from __future__ import annotations

import base64
import hashlib
import io
import os
import shutil
import tarfile
import urllib.request
from pathlib import Path

from .content import ROOT

VERSION = "22.0.0-git20542-10"
TARBALL = f"https://registry.npmjs.org/@yowasp/clang/-/clang-{VERSION}.tgz"
INTEGRITY = "sha512-V31/z9GrJECKeACTDUyvg2llbEUiFi3bxtL5HSg7H/6sydSxdI/AoVAD9F5ozrfLjotWl0B9rghR87m+DUM/zg=="
FILES = ("bundle.js", "llvm.core.wasm", "llvm.core2.wasm", "llvm.core3.wasm", "llvm.core4.wasm",
         "llvm-resources.tar")

DIRNAME = f"clang-{VERSION}"            # versioned, so the server can mark it immutable
VENDOR = ROOT / "vendor" / DIRNAME
WEB_PATH = f"/static/vendor/{DIRNAME}"  # relative to the site base


def available() -> bool:
    return all((VENDOR / f).is_file() for f in FILES)


def fetch(quiet: bool = False) -> bool:
    """Download and unpack the toolchain into vendor/ if it isn't there. Returns True on success."""
    if available():
        return True
    if os.environ.get("CPPGUIDE_NO_FETCH"):
        return False
    if not quiet:
        print(f"Downloading the in-browser C++ compiler (Clang {VERSION}, ~40 MB, one time)…")
    try:
        with urllib.request.urlopen(TARBALL, timeout=300) as resp:
            data = resp.read()
    except OSError as exc:
        print(f"warning: couldn't download the WASM compiler ({exc}); in-browser runs are disabled.")
        return False
    algo, _, want = INTEGRITY.partition("-")
    got = base64.b64encode(hashlib.new(algo, data).digest()).decode()
    if got != want:
        print("warning: WASM compiler download failed its integrity check; in-browser runs are disabled.")
        return False
    tmp = VENDOR.with_name(DIRNAME + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for name in FILES:
            member = tar.getmember(f"package/gen/{name}")
            with tar.extractfile(member) as src, open(tmp / name, "wb") as dst:
                shutil.copyfileobj(src, dst)
    shutil.rmtree(VENDOR, ignore_errors=True)
    tmp.rename(VENDOR)
    return True


def install(site_static: Path) -> bool:
    """Hard-link (or copy) the toolchain into the built site. Cheap enough to do on every rebuild."""
    if not available():
        return False
    dest = site_static / "vendor" / DIRNAME
    dest.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        try:
            os.link(VENDOR / name, dest / name)
        except OSError:
            shutil.copy2(VENDOR / name, dest / name)
    return True
