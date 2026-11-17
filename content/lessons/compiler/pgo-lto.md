+++
title = "LTO, PGO and Build Configuration"
minutes = 40
practice_tags = ["toolchain", "inline"]
+++

## Why this chapter matters

The compiler optimizes one translation unit at a time, guessing which branches are likely and which functions are
hot. Two techniques remove those limits: **link-time optimization (LTO)** lets it optimize across the whole program,
and **profile-guided optimization (PGO)** replaces its guesses with measurements from a real run. Large projects such
as browsers, compilers and databases ship with both and report double-digit percentage gains.

But here is a small simulated feed handler (20 million messages through a `switch` on message type, with a skewed
type mix and a cold error path), measured on the test machine:

| Build | ns per message |
|---|---|
| `-O2` | 10.1 |
| `-O2 -flto` | 9.5 |
| `-O2` + PGO | 11.4 |

LTO helped by 7%. PGO made this program **12% slower**, consistently across runs. That isn't a fluke of measurement;
it's a reminder that these are powerful tools whose effects depend on the program. This chapter explains how each
works, what it's good at, how to set up a build that uses them, and why you must measure.

By the end of this chapter you should be able to:

- explain what LTO does, and set it up with GCC, Clang and CMake;
- explain what PGO does, run the instrument–train–optimize cycle, and avoid common mistakes;
- predict which programs benefit from PGO and which don't;
- choose a sensible release build configuration for a latency-sensitive system.

---

## 1. Link-time optimization

Normally each `.cpp` file is compiled to machine code independently, and the linker just stitches the pieces
together. A function defined in `pricing.cpp` is opaque to code in `strategy.cpp`: it can't be inlined, its
arguments can't be constant-propagated, and its side effects must be assumed to be anything (Chapter 8.1).

With `-flto`, the compiler writes its **intermediate representation** (GIMPLE for GCC, LLVM bitcode for Clang) into
the object files. At link time the linker hands all of it back to the optimizer, which now sees the whole program:

- **cross-module inlining**: the fee function from Chapter 8.1, defined in another file, went from 1.58 to 0.65 ns
  per element with LTO;
- **whole-program devirtualization**: if only one class overrides a virtual function, calls can become direct;
- **dead code elimination** across files, and removal of unused functions;
- **interprocedural constant propagation**: a parameter that's always passed the same constant can be folded.

```bash
g++ -O2 -flto=auto -c a.cpp b.cpp        # =auto: use all cores for the link-time step
g++ -O2 -flto=auto a.o b.o -o app        # the optimization happens here; pass the same -O and -march flags
```

Clang's **ThinLTO** (`-flto=thin`) does most of the same work with much less memory and better parallelism, by
summarizing each module and importing only the functions worth inlining. It's the practical choice for large
codebases.

In CMake:

```cmake
include(CheckIPOSupported)
check_ipo_supported(RESULT has_lto)
if(has_lto)
  set(CMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE ON)       # or per target: INTERPROCEDURAL_OPTIMIZATION
endif()
```

Costs: link times grow (sometimes a lot, for full LTO), debugging optimized code gets harder, and static libraries
must be built with LTO too, or their code stays opaque. LTO rarely makes code slower; it's a good default for release
builds of performance-sensitive programs.

---

## 2. Profile-guided optimization

Many optimization decisions depend on how often code runs: which side of a branch to make the fall-through, which
functions to inline, which loops to unroll or vectorize, how to lay out functions in memory, whether to
speculatively devirtualize a call. Without data, the compiler guesses with heuristics (loops run many times, error
paths are rare, and so on). PGO gives it real counts.

The cycle has three steps:

```bash
# 1. Instrument: build with counters on every branch and call
g++ -O2 -fprofile-generate main.cpp -o app

# 2. Train: run representative workloads; each run writes .gcda count files
./app --replay normal_day.bin
./app --replay busy_open.bin

# 3. Optimize: rebuild using the counts (same source, same output name, same flags otherwise)
g++ -O2 -fprofile-use main.cpp -o app
```

(Clang uses `-fprofile-instr-generate`, `llvm-profdata merge`, and `-fprofile-instr-use`, or sampling-based profiles
collected with `perf` from a normal production binary, via AutoFDO.)

With the profile, the compiler:

- lays out **hot paths as straight-line fall-through code** and moves cold blocks and cold functions into separate
  sections (`.text.hot`, `.text.unlikely`), which improves instruction-cache and TLB use;
- **inlines** based on actual call frequency rather than size heuristics;
- **speculatively devirtualizes** indirect calls to their most common targets;
- reorders `switch` cases and if-chains by frequency, and tunes unrolling and vectorization.

> [!WARNING]
> A practical trap: with GCC 11 and later, the profile file is named after the *output* file. If the instrumented
> build is `-o gen` and the optimized build is `-o app`, GCC looks for `app-main.gcda`, doesn't find it, prints a
> `-Wmissing-profile` warning, and quietly builds without profile data. Keep the output name the same, or use
> `-fprofile-dir`, and treat `-Wmissing-profile` as an error.

### Why PGO didn't help the feed handler

The feed-handler benchmark is close to the worst case for PGO:

- **Its dominant cost is unpredictable branches on random data.** Message types arrive in random order, so the
  dispatch mispredicts often (Chapter 5.4). A profile tells the compiler how *often* each type occurs, not how to
  predict the *next* one; no code layout fixes that.
- **It's tiny.** All of its hot code fits in the instruction cache whatever the layout, so the biggest real-world
  benefit of PGO, better code layout for large programs with instruction-cache pressure, has nothing to work on.
- **The static heuristics were most likely already close to right**, so the profile mainly led the compiler to make
  different trade-offs (more inlining and unrolling in some places, less in others), which on this program happened to cost more
  than they gained.

Programs that benefit most are the opposite: **large** (megabytes of code, deep call graphs), with **branches that
are biased but not obviously so** to static heuristics, and **indirect calls** dominated by a few targets. A full
trading application, with its parsers, book, strategy, risk and gateway, is much more like that than this benchmark.

### Training data is everything

PGO optimizes for the workload it was trained on. A profile collected on a quiet day can make a different path cold,
and a busy open, or the rare "send an order" path, can end up laid out as unlikely code. For trading systems:

- train on **replayed production data** covering normal and busy periods;
- make sure the **critical but rare paths** (order sending, risk rejections, reconnects) are exercised, or the
  compiler will treat them as cold; this is the PGO version of Chapter 12.2's warm-up problem;
- regenerate profiles regularly as code changes; stale profiles (`-Wcoverage-mismatch`) are partially ignored.

### Exercises

:::exercise Choose training data
A team trains PGO by running their engine for 10 minutes in a test environment with synthetic market data and no
order flow (the strategy never decides to trade). What will PGO likely do to the latency of sending an order in
production?
:::solution
The order-sending path never ran during training, so the profile marks it as never executed: PGO will lay it out as
cold code, possibly move it to `.text.unlikely` away from the hot code, avoid inlining into it, and optimize it for
size. In production, the first (and every) order send then runs code that's laid out badly and more likely to miss
in the instruction cache, precisely on the path that matters most. The training run must exercise the full
tick-to-trade path, e.g. by replaying real data with the strategy configured to trade (against a simulator).
:::

---

## 3. A release build configuration

Putting the last few chapters together, a reasonable configuration for a latency-sensitive production binary:

```bash
-std=c++20
-O2                         # or -O3, if measured better for this program (Chapter 1.2)
-march=x86-64-v3            # or the exact production CPU, e.g. -march=icelake-server; never -march=native on a build box
-g                          # debug info: no runtime cost, essential for profiling and crash analysis
-fno-omit-frame-pointer     # cheap, reliable stack traces for perf (Chapter 6.2)
-flto=auto                  # cross-module inlining (ThinLTO with Clang)
-fno-plt -fvisibility=hidden    # direct calls to shared-library functions; no symbol interposition (Chapter 8.1)
-Wall -Wextra -Werror ...   # warnings as errors in CI
# plus PGO, if measured to help, with representative training
```

And keep, alongside it:

- a **debug build** (`-O0 -g` or `-Og`) for stepping through code;
- a **sanitizer build** (`-O1 -g -fsanitize=address,undefined`, and a separate TSan build) that runs the full test
  suite and replay tests in CI;
- a **benchmark build** identical to release, so benchmark results transfer.

> [!PERF]
> Every flag change is a performance change and must be measured on the full system with realistic data, not just a
> microbenchmark. The PGO result in this chapter is a small example of why: a technique that typically helps large
> applications made a small one slower. Keep a benchmark history so you notice when a compiler upgrade or flag
> change moves your latency distribution.

---

## Summary

- LTO optimizes the whole program at link time: cross-file inlining, devirtualization, dead-code removal. ThinLTO
  scales it to large codebases. It rarely hurts; it helped by 7% here.
- PGO feeds real execution counts back into optimization: hot/cold layout, inlining, speculative devirtualization,
  branch ordering. It helps most in large programs with instruction-cache pressure and biased branches.
- PGO can't fix unpredictable data, and can hurt: it made this chapter's small benchmark 12% slower.
- Train PGO on replayed production data that exercises rare critical paths; keep output names consistent so the
  profile is actually used.
- Pin `-march` to the deployment CPU, keep `-g` and frame pointers, and measure every configuration change on the
  whole system.

## Key terms

**Link-time optimization (LTO)** · **ThinLTO** · **intermediate representation** · **whole-program
devirtualization** · **profile-guided optimization (PGO)** · **instrumentation** · **training run** · **AutoFDO** ·
**hot/cold splitting** · **code layout** · **stale profile**
