+++
title = "Compilers, Flags and Sanitizers"
minutes = 30
practice_tags = ["toolchain", "ub"]
+++

## Why this chapter matters

Here are three true-to-life bugs:

- A strategy works perfectly in your debug build. In the release build at `-O2` it occasionally sends orders with
  a quantity of zero. The cause is an uninitialized variable that happened to be zero at `-O0`.
- An off-by-one loop writes one `double` past the end of a `bids[]` array, straight into the neighbouring
  `asks[]` array. Nothing crashes. The P&L is just slightly wrong, for weeks.
- A colleague's benchmark "proves" that `std::vector` is 40× slower than a raw array. They compiled without
  optimization.

Each of these is caught or prevented by **how you invoke the compiler**. The compiler is the best bug-finder and
performance tool you have, but its defaults are tuned for backward compatibility, not for you. This chapter sets
up a build you can trust: maximum warnings, sanitizers in development, and the right optimization flags when you
measure.

By the end of this chapter you should be able to:

- pick a language standard and a sensible set of warning flags;
- explain what `-O0` through `-O3`, `-march=native` and `-g` each do;
- use AddressSanitizer, UndefinedBehaviorSanitizer and ThreadSanitizer, and read their reports;
- write a minimal CMake project with debug and release configurations.

---

## 1. Compilers and language standards

There are three major C++ compilers: **GCC** (`g++`), **Clang** (`clang++`) and **MSVC** (`cl.exe`). Trading
firms overwhelmingly deploy on Linux with GCC or Clang. It's worth having both installed: they catch different
warnings, and the faster one varies by workload.

C++ is standardized every three years: C++11, 14, 17, 20, 23. Always say which standard you want explicitly. The
default differs across compilers and versions:

```bash
g++ -std=c++20 main.cpp
```

This book targets **C++20**, which gives us concepts, ranges, `std::span`, designated initializers,
`[[likely]]`, `consteval`, `std::bit_cast`, and more. Where a C++23 feature such as `std::expected` matters we'll
point it out.

### Exercises

:::exercise Which standard?
Run `g++ -dM -E -x c++ /dev/null | grep __cplusplus`, then again with `-std=c++20`. What values do you see, and
what does the macro tell you?
:::solution
`__cplusplus` is set to a date-like value for each standard: `201703L` for C++17, `202002L` for C++20, `202302L`
for C++23. The first command shows your compiler's default (C++17 for GCC 11–15). Code can test this macro, or
the more specific feature-test macros like `__cpp_concepts`, to adapt to the standard it's compiled under.
:::

---

## 2. Warnings: make the compiler argue with you

A **warning** is the compiler saying "this is legal, but you probably didn't mean it." By default very few are
enabled. Here's a good baseline:

```bash
g++ -std=c++20 -Wall -Wextra -Wpedantic -Wshadow -Wconversion -Wsign-conversion main.cpp
```

| Flag | Catches |
|---|---|
| `-Wall -Wextra` | the core set: unused variables, missing returns, signed/unsigned comparisons, and more |
| `-Wpedantic` | non-standard extensions |
| `-Wshadow` | a local variable hiding another (a classic source of "why didn't my member update?") |
| `-Wconversion` | implicit narrowing, e.g. `double` → `int`, `int64_t` → `int32_t` |
| `-Wsign-conversion` | implicit signed ↔ unsigned conversions |
| `-Werror` | turn warnings into errors (use in CI so warnings can't accumulate) |

`-Wconversion` is particularly valuable in finance code. Look at this:

```cpp
#include <cstdint>

int32_t notional_cents(double price, int32_t qty) {
    return price * 100 * qty;     // -Wconversion: double -> int32_t may change value
}
```

A price of $500 and a quantity of 100,000 gives 5,000,000,000 cents, which doesn't fit in 32 bits. Without the
warning this compiles silently and produces garbage. (Converting an out-of-range floating-point value to an
integer is actually *undefined behaviour*.)

### Exercises

:::exercise Spot the warnings
Which warning flags would fire on this function, and what is each one telling you?

```cpp
int count_fills(const std::vector<int>& fills) {
    int count = 0;
    for (int i = 0; i < fills.size(); ++i) {
        int count = fills[i] > 0;
        (void)count;
    }
}
```
:::solution
- `-Wsign-compare` (in `-Wall`): `i < fills.size()` compares a signed `int` with an unsigned `size_t`.
- `-Wshadow`: the inner `int count` hides the outer `count`. The outer one is never updated, which is a real bug.
- `-Wreturn-type` (in `-Wall`): the function returns `int` but has no `return` statement. Falling off the end of
  a non-void function is **undefined behaviour**, and at `-O2` GCC may generate code that runs straight into
  whatever function comes next in memory.
:::

---

## 3. Optimization levels

| Flag | What it does | When to use it |
|---|---|---|
| `-O0` | No optimization. Every variable lives in memory, and the code maps 1:1 to the source. | Stepping through in a debugger |
| `-O1` | Basic optimizations that don't take long to compile | Rarely chosen explicitly |
| `-O2` | Nearly all optimizations that don't trade code size for speed: inlining, vectorization (GCC ≥ 12), scheduling | **The default for release builds** |
| `-O3` | More aggressive inlining, loop unrolling and vectorization | Benchmark it: sometimes faster, sometimes slower than `-O2` |
| `-Os` / `-Oz` | Optimize for size | Embedded systems, or code where i-cache pressure dominates |
| `-Ofast` | `-O3` plus `-ffast-math`, which breaks IEEE floating-point rules | Almost never in finance (see Chapter 14.1) |

Two other flags matter a lot:

- **`-march=native`** lets the compiler use every instruction your CPU supports (AVX2, AVX-512, BMI2, ...).
  It can give large speedups for numeric code, but the binary may crash with `SIGILL` on an older machine. In
  production, pin an explicit target that matches your servers, e.g. `-march=icelake-server`.
- **`-g`** adds debug information. It doesn't make code slower and it's independent of `-O`. Always build release
  binaries with `-O2 -g` so profilers and crash dumps can show source lines.

> [!WARNING]
> **Never benchmark at `-O0`.** Unoptimized code doesn't inline anything, so abstractions like `std::vector`'s
> `operator[]`, iterators and lambdas look enormously expensive when they normally cost nothing. Any performance
> conclusion drawn at `-O0` is meaningless.

### Exercises

:::exercise -O0 vs -O2
Compile `int sum(const int* a, int n) { int s = 0; for (int i = 0; i < n; ++i) s += a[i]; return s; }` with
`-O0 -S` and `-O2 -S` (or on Compiler Explorer, godbolt.org). Describe two differences in the assembly.
:::solution
At `-O0`, `s`, `i` and the arguments are stored on the stack and reloaded on every iteration (lots of
`mov ... [rbp-8]`). At `-O2` everything stays in registers, and with GCC ≥ 12 or Clang the loop is
**vectorized**: it uses `paddd` on `xmm` registers to add four ints at a time, with a scalar loop at the end for
the leftovers. Often the `-O2` loop body runs 10× or more faster.
:::

:::exercise The SIGILL deploy
You build with `-march=native` on your shiny new workstation and deploy to the colocation servers. The binary
crashes immediately with "Illegal instruction". Why, and how do you prevent it?
:::solution
`-march=native` let the compiler emit instructions (say AVX-512) that the older colo CPUs don't implement. When
the CPU hits one it raises `SIGILL`. Prevent it by compiling for the deployment target explicitly
(`-march=skylake-avx512`, `-march=x86-64-v3`, ...) or by building on, or for, the same hardware you deploy to.
:::

---

## 4. Sanitizers: runtime bug detectors

Warnings catch problems the compiler can see statically. **Sanitizers** instrument your program so it catches
bugs *as they happen* at run time, with a precise report. They cost roughly 1.5–3× in speed, so use them in
development and CI, not in production.

| Sanitizer | Flag | Catches |
|---|---|---|
| AddressSanitizer (ASan) | `-fsanitize=address` | out-of-bounds reads and writes, use-after-free, double free, memory leaks |
| UndefinedBehaviorSanitizer (UBSan) | `-fsanitize=undefined` | signed overflow, invalid shifts, null dereferences, misaligned access, bad casts |
| ThreadSanitizer (TSan) | `-fsanitize=thread` | data races between threads |
| MemorySanitizer (MSan, Clang only) | `-fsanitize=memory` | reads of uninitialized memory |

ASan and UBSan can be combined. TSan must be used on its own. Add `-fno-omit-frame-pointer -g` for good stack
traces.

Without sanitizers the compiler is free to place the two arrays next to each other on the stack. In that case
the out-of-bounds write `bids[5]` lands exactly on top of another variable:

```layout "Without ASan: bids[5] is one past the end of bids, which may be asks[0] (or a saved register, or the return address). ASan surrounds every array with poisoned 'redzones' so the bad write is caught instead."
title: a possible stack layout without ASan (5 doubles per row)
row: 40
bids[0]:8:1 bids[1]:8:1 bids[2]:8:1 bids[3]:8:1 bids[4]:8:1 asks[0]_←_bids[5]:8:4 asks[1]:8:2 asks[2]:8:2 asks[3]:8:2 asks[4]:8:2

title: with ASan: redzones around each array
row: 40
redzone:8:6 bids[0]:8:1 bids[1]:8:1 bids[2]:8:1 bids[3]:8:1 bids[4]:8:1 redzone_←_bids[5]:32:6 asks[0]:8:2 asks[1]:8:2 asks[2]:8:2 asks[3]:8:2 asks[4]:8:2 redzone:24:6
```

Here's the silent `bids`/`asks` bug from the introduction:

```cpp oob.cpp
#include <cstdio>

int main() {
    double bids[5] = {}, asks[5] = {};
    for (int i = 0; i <= 5; ++i)      // bug: <= should be <
        bids[i] = 100.0 - i * 0.01;
    std::printf("%f\n", asks[0]);
}
```

```bash
$ g++ -std=c++20 -g -fsanitize=address,undefined oob.cpp && ./a.out
==12345==ERROR: AddressSanitizer: stack-buffer-overflow on address 0x7ffd...
WRITE of size 8 at 0x7ffd... thread T0
    #0 0x... in main oob.cpp:6
...
  This frame has 2 object(s):
    [32, 72) 'bids' (line 4) <== Memory access at offset 72 overflows this variable
```

It tells you the exact line, whether it was a read or a write, and which variable overflowed. The practice
problems on this site always run with ASan and UBSan, so you'll get used to reading these reports.

### Exercises

:::exercise Which sanitizer?
Which sanitizer catches each bug? (a) `int x = INT_MAX; x += 1;` (b) returning a pointer to a local array and
reading through it later; (c) two threads incrementing a plain `long counter` with no lock; (d) `1 << 40` where
the `1` is an `int`.
:::solution
(a) UBSan: signed integer overflow. (b) ASan: `stack-use-after-return` (enable it with
`ASAN_OPTIONS=detect_stack_use_after_return=1`; Clang 15+ turns it on by default). (c) TSan: a data race.
(d) UBSan: shift exponent too large for a 32-bit type.
:::

:::exercise Why not in production?
Give two reasons sanitizers aren't used in production trading binaries.
:::solution
(1) Overhead: ASan roughly doubles run time and memory and adds latency to every memory access, which is
unacceptable on a hot path. (2) ASan reserves a huge "shadow memory" region and changes the allocator, so it
interferes with huge pages, custom allocators and kernel-bypass libraries. Its reports also abort the process,
which isn't the failure mode you want in production. The answer is to run them extensively in testing,
replaying production data.
:::

---

## 5. A minimal CMake setup

Typing flags by hand doesn't scale. **CMake** is the de facto standard build-system generator. A minimal project:

```cmake CMakeLists.txt
cmake_minimum_required(VERSION 3.20)
project(quant_playground LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)   # for clangd / editor tooling

add_compile_options(-Wall -Wextra -Wpedantic -Wshadow -Wconversion)

option(SANITIZE "Build with ASan + UBSan" OFF)
if(SANITIZE)
  add_compile_options(-fsanitize=address,undefined -fno-omit-frame-pointer)
  add_link_options(-fsanitize=address,undefined)
endif()

add_executable(book_bench src/book_bench.cpp)
```

```bash
cmake -S . -B build/debug   -DCMAKE_BUILD_TYPE=Debug -DSANITIZE=ON
cmake -S . -B build/release -DCMAKE_BUILD_TYPE=RelWithDebInfo
cmake --build build/release -j
```

The build types: `Debug` (`-O0 -g`), `Release` (`-O3 -DNDEBUG`), and `RelWithDebInfo` (`-O2 -g -DNDEBUG`). Use
`RelWithDebInfo` for anything you'll profile.

> [!TIP]
> Keep **separate build directories** for each configuration. Switching between debug and release then costs
> nothing, and you'll never accidentally benchmark a debug build.

### Exercises

:::exercise NDEBUG
`Release` and `RelWithDebInfo` both define `NDEBUG`. What does that do to `assert(qty > 0);` in your order code?
Why might that be a problem, and what could you do instead?
:::solution
With `NDEBUG` defined, `assert` expands to nothing, so the check disappears from release builds. If that check
was protecting against something that can genuinely happen in production, like bad input from an exchange or a
user, it's gone exactly when it matters. Use `assert` only for programmer-error invariants. Validate external
input with real checks that stay in release builds (ideally marked `[[unlikely]]` so they cost almost nothing on
the hot path).
:::

---

## Summary

- Always pass `-std=c++20` and a strong warning set; add `-Werror` in CI.
- `-O2 -g` for release, profiling and benchmarks. **Never measure at `-O0`.**
- `-march=native` unlocks modern instructions but ties the binary to the CPU.
- Run development builds and tests under **ASan + UBSan**, and concurrent code under **TSan**.
- Use CMake with separate build directories for each configuration.

## Key terms

**Language standard** · **warning** · **optimization level** · **target architecture** · **sanitizer** ·
**shadow memory** · **build type**
