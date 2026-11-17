# cpp-guide

A textbook and practice site for high-performance C++ in quantitative finance.

```bash
uv run cpp-guide serve
```

Requires [uv](https://docs.astral.sh/uv/) and `g++` (C++20).

Practice problems compile and run **in the browser** by default: the first build downloads a pinned,
integrity-checked [YoWASP Clang](https://github.com/YoWASP/clang) (Clang 22 built for WebAssembly) into
`vendor/` and links it into `site/static/vendor/`. The WASM build has no exceptions or threads, so the
few problems that need them can switch to the server runner (g++ with ASan/UBSan) from the editor.
Set `CPPGUIDE_NO_FETCH=1` to skip the download.
