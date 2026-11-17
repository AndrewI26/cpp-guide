# cpp-guide

A textbook and practice site for high-performance C++ in quantitative finance.

```bash
uv run cpp-guide serve
```

Requires [uv](https://docs.astral.sh/uv/). `uv run cpp-guide check` (verifies reference solutions locally) also needs `g++`.

Practice problems compile and run **in the browser**, never on the server: the first build downloads a
pinned, integrity-checked [YoWASP Clang](https://github.com/YoWASP/clang) (Clang 22 built for WebAssembly) into
`vendor/` and links it into `site/static/vendor/`. The WASM build has no exceptions or threads, so problems
that need them can't be run on the site. Set `CPPGUIDE_NO_FETCH=1` to skip the download.
