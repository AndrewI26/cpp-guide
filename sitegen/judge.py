"""Compile and run practice-problem submissions locally with g++.

A submission is the contents of `solution.hpp`. The problem's Tests section is a
`main()` that includes it and uses the CHECK macros from judge.hpp below.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import time

JUDGE_HPP = r"""#pragma once
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

namespace judge {
inline int passed = 0, failed = 0;

template <class T> std::string show(const T& v) {
    std::ostringstream os;
    if constexpr (requires { os << v; }) { os << v; }
    else if constexpr (requires { v.begin(); v.end(); }) {
        os << '{'; bool first = true;
        for (const auto& x : v) { os << (first ? "" : ", ") << show(x); first = false; }
        os << '}';
    } else { os << "<unprintable>"; }
    return os.str();
}

inline int report() {
    std::cout << "\n" << passed << " passed, " << failed << " failed\n";
    return failed ? 1 : 0;
}
}  // namespace judge

#define CHECK(expr) do { if (expr) ++judge::passed; else { ++judge::failed; \
    std::cout << "FAIL (line " << __LINE__ << "): " #expr "\n"; } } while (0)

#define CHECK_EQ(a, b) do { auto&& _a = (a); auto&& _b = (b); \
    if (_a == _b) ++judge::passed; else { ++judge::failed; \
    std::cout << "FAIL (line " << __LINE__ << "): " #a " == " #b "\n    got:      " \
              << judge::show(_a) << "\n    expected: " << judge::show(_b) << "\n"; } } while (0)

#define CHECK_NEAR(a, b, eps) do { double _a = (a), _b = (b); \
    if (std::fabs(_a - _b) <= (eps)) ++judge::passed; else { ++judge::failed; \
    std::cout << "FAIL (line " << __LINE__ << "): " #a " ~= " #b "\n    got:      " << _a \
              << "\n    expected: " << _b << " (eps " << (eps) << ")\n"; } } while (0)
"""

CXX = os.environ.get("CXX", "g++")
CXXFLAGS = ["-std=c++20", "-O2", "-g", "-Wall", "-Wextra", "-pthread",
            "-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
MAX_OUTPUT = 20_000
MAX_JOBS = int(os.environ.get("CPPGUIDE_MAX_JOBS", "2"))
_slots = threading.BoundedSemaphore(MAX_JOBS)   # caps concurrent compiles/runs


def compiler_available() -> bool:
    return shutil.which(CXX) is not None


def run(solution: str, tests: str, compile_timeout: int = 60, run_timeout: int = 10) -> dict:
    if not _slots.acquire(timeout=90):
        return {"ok": False, "stage": "setup", "output": "The runner is busy. Try again in a moment."}
    try:
        return _run(solution, tests, compile_timeout, run_timeout)
    finally:
        _slots.release()


def _run(solution: str, tests: str, compile_timeout: int, run_timeout: int) -> dict:
    if not compiler_available():
        return {"ok": False, "stage": "setup", "output": f"Compiler '{CXX}' not found on PATH."}
    if not tests.strip():
        return {"ok": False, "stage": "setup", "output": "This problem has no tests yet."}
    with tempfile.TemporaryDirectory(prefix="cppguide-") as tmp:
        with open(os.path.join(tmp, "solution.hpp"), "w") as f:
            f.write(solution)
        with open(os.path.join(tmp, "judge.hpp"), "w") as f:
            f.write(JUDGE_HPP)
        with open(os.path.join(tmp, "main.cpp"), "w") as f:
            f.write('#include "solution.hpp"\n#include "judge.hpp"\n#line 1 "tests.cpp"\n' + tests)
        exe = os.path.join(tmp, "prog")
        t0 = time.perf_counter()
        try:
            cp = subprocess.run([CXX, *CXXFLAGS, "main.cpp", "-o", exe], cwd=tmp,
                                capture_output=True, text=True, timeout=compile_timeout)
        except subprocess.TimeoutExpired:
            return {"ok": False, "stage": "compile", "output": "Compilation timed out."}
        compile_ms = int((time.perf_counter() - t0) * 1000)
        if cp.returncode != 0:
            return {"ok": False, "stage": "compile", "output": cp.stderr[-MAX_OUTPUT:], "compile_ms": compile_ms}
        t1 = time.perf_counter()
        try:
            rp = subprocess.run([exe], cwd=tmp, capture_output=True, text=True, timeout=run_timeout,
                                env={**os.environ, "ASAN_OPTIONS": "detect_leaks=1"})
        except subprocess.TimeoutExpired:
            return {"ok": False, "stage": "run", "output": f"Time limit exceeded ({run_timeout}s).",
                    "compile_ms": compile_ms}
        run_ms = int((time.perf_counter() - t1) * 1000)
        output = (rp.stdout + rp.stderr)[-MAX_OUTPUT:]
        return {"ok": rp.returncode == 0, "stage": "run", "output": output,
                "compile_ms": compile_ms, "run_ms": run_ms, "warnings": cp.stderr[-MAX_OUTPUT:]}
