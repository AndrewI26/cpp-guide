+++
title = "Inlining and Call Overhead"
minutes = 40
practice_tags = ["inline", "static-polymorphism"]
+++

## Why this chapter matters

The same fee calculation, `px * 0.0001 + 0.01`, applied to 4,096 prices in a loop, measured on the test machine at
`-O3 -march=native`:

| How the fee function is called | ns per price |
|---|---|
| defined in another `.cpp` file | 1.68 |
| virtual call through a base-class reference | 1.65 |
| virtual call on a `final` class | 0.27 |
| defined inline in the header | 0.25 |
| defined in another `.cpp` file, built with `-flto` | 0.65 (at `-O2`, same as inline at `-O2`) |

A function call that can't be inlined makes this loop **6.7× slower**. The call itself only costs a few cycles; the
real loss is that the loop can no longer be vectorized, because the compiler can't see what happens inside the call.
Chapter 2.1 explained the `inline` keyword and where definitions should live. This chapter is about the optimizer's
side: how it decides what to inline, what stops it, how virtual calls get devirtualized, and how to check and steer
the result.

By the end of this chapter you should be able to:

- explain why inlining matters more for the optimizations it enables than for the call it removes;
- describe the compiler's inlining heuristics and the main things that prevent inlining;
- get devirtualization with `final`, and inlining across files with LTO;
- use compiler reports and attributes to check and control inlining decisions.

---

## 1. What inlining buys

**Inlining** replaces a call with a copy of the callee's body, specialized for the call site. The direct saving is
the call overhead: argument setup, `call`/`ret`, prologue and epilogue, and caller-saved registers spilled around the
call (Chapter 2.1). That's a handful of cycles.

The indirect savings are usually far larger, because after inlining, the callee's code is optimized **together
with** the caller's:

- **Constant propagation.** If the caller passes constants, the inlined body is simplified for them: branches on
  them disappear.
- **Vectorization.** A loop containing an opaque call can't be vectorized; a loop containing the inlined body often
  can. That's the 6.7× above.
- **Redundancy elimination.** Values computed in the caller don't need to be recomputed or reloaded in the callee,
  and vice versa.
- **No aliasing pessimism across the call.** Across an opaque call, the compiler must assume any memory reachable
  from globals or pointers may have changed, so it reloads values afterwards. Inlined, it knows exactly what's
  written.

So for small functions on hot paths, being inlinable is close to a requirement. That's why the standard library's
containers, iterators and algorithms are all templates defined in headers.

---

## 2. How the compiler decides

The compiler inlines when it can see the callee's body and its heuristics judge it worthwhile. GCC and Clang
consider, roughly:

- **Size of the callee**, measured in internal instructions after early optimization. Small functions (getters,
  arithmetic helpers, operator overloads) are almost always inlined.
- **Call-site context**: calls in loops get a bonus; calls on paths marked cold, or after `[[unlikely]]` branches,
  get a penalty.
- **Growth limits**: total growth of the caller and of the whole translation unit is capped to limit code bloat.
- **Single call site**: a `static` or anonymous-namespace function called once is inlined regardless of size, since
  inlining it doesn't duplicate any code.
- **`-O` level**: `-O2` inlines functions declared `inline` (or in a class body) and small others; `-O3` is more
  aggressive. At `-O0` nothing is inlined except `always_inline`.

Things that **prevent** inlining:

| Obstacle | Fix |
|---|---|
| definition in another translation unit | move it to a header, or use LTO (section 4) |
| call through a function pointer or `std::function` | template on the callable type (Chapter 2.5) |
| virtual call where the dynamic type isn't known | `final`, or static polymorphism (Chapter 3.6) |
| recursion | usually can't be fully inlined |
| the callee is too big, or the caller has grown too much | split hot and cold parts of the callee; `always_inline` as a last resort |
| exported from a shared library with default visibility | `-fvisibility=hidden`, or `-fno-semantic-interposition` |

The last row is a frequent surprise: in a shared library built with `-fPIC`, any non-`static` function with default
visibility can be replaced at load time (symbol interposition), so the compiler won't inline it even within the same
file. Hiding symbols that aren't part of the library's API fixes it.

### Seeing the decisions

```bash
g++ -O2 -fopt-info-inline-optimized-missed=inline.txt file.cpp    # GCC: what was inlined, and why not
clang++ -O2 -Rpass=inline -Rpass-missed=inline file.cpp           # Clang: remarks per call site
```

Or just look for `call` instructions in the hot loop's assembly (Chapter 6.3).

### Exercises

:::exercise Why wasn't it inlined?
For each, explain why the call in a hot loop probably isn't inlined: (a) `double Pricer::price(const Option&)` is
declared in `pricer.hpp` and defined in `pricer.cpp`; (b) `std::function<double(double)> fee` is called per element;
(c) `strategy->on_quote(q)` where `strategy` is a `Strategy*` to one of several derived classes; (d) a 400-line
`handle_message` called from one place in `main.cpp`, but declared `extern` and also used by a test binary.
:::solution
(a) The body is in another TU; without LTO the compiler can't see it. (b) `std::function` type-erases the callable,
so the call is indirect through a function pointer. (c) A virtual call with an unknown dynamic type. (d) It's large,
and because it has external linkage it must also exist as a standalone function, so the "single call site" bonus
doesn't apply; inlining would duplicate 400 lines. That's probably the right decision; its *callees* matter more.
:::

---

## 3. Devirtualization

A virtual call can be inlined only if the compiler proves which override will run. It can do this when:

- the object's dynamic type is known at the call site (it was just constructed locally, or is a value, not a
  reference or pointer);
- the class, or the specific function, is marked **`final`**, so no further overrides can exist;
- with LTO and `-fwhole-program-vtables` (Clang) or `-fdevirtualize-at-ltrans` (GCC), the whole program shows that
  only one override exists;
- **speculative devirtualization**: the compiler (especially with profile data, Chapter 8.4) guesses the likely
  target and emits `if (vptr == expected) inlined_body; else virtual_call;`.

```cpp
struct Fee { virtual ~Fee() = default; virtual double operator()(double px) const = 0; };
struct LinearFee final : Fee {                                      // final: no further overrides possible
    double operator()(double px) const override { return px * 0.0001 + 0.01; }
};

void apply(const LinearFee& f, const double* px, double* out, int n) {
    for (int i = 0; i < n; ++i) out[i] = f(px[i]);   // devirtualized and inlined: 0.27 ns/elem measured
}
```

The measured difference between the `final` and non-`final` versions is 6×. Marking leaf classes `final` costs
nothing and should be a habit.

---

## 4. Inlining across translation units: LTO

**Link-time optimization** (`-flto`) makes the compiler write its intermediate representation into the object files
instead of (or in addition to) machine code. At link time, the whole program's IR is optimized together, so
functions in different `.cpp` files can be inlined into each other. In the measurement above, the fee function
defined in another file went from 1.58 ns per element to 0.65 ns at `-O2` with `-flto`, the same as the header-inline
version.

LTO gets a full treatment in Chapter 8.4, together with profile-guided optimization. For inlining, the takeaway is:
LTO lets you keep definitions in `.cpp` files (better compile times and encapsulation) without losing cross-file
inlining.

---

## 5. Steering the inliner

Compilers usually decide well. When measurement shows they didn't, you can override them:

```cpp
[[gnu::always_inline]] inline double mid(std::int64_t b, std::int64_t a) { return (b + a) / 2.0; }

[[gnu::noinline, gnu::cold]] void report_bad_message(const Msg& m);   // keep error handling out of the hot path

[[gnu::flatten]] void on_message(const Msg& m);                       // inline everything called from here, recursively
```

- **`always_inline`** forces inlining (and errors if impossible). Use it for tiny functions in hot paths that the
  heuristics occasionally refuse, for example after a caller grew past a limit.
- **`noinline`** keeps a function out of line. Combined with **`cold`**, it moves rarely used code (error reporting,
  logging, recovery) away from the hot path, shrinking the hot code and improving instruction-cache use.
- **`flatten`** inlines every call inside the function, as deeply as possible. Powerful for a hot dispatch function,
  but it can explode code size.
- MSVC equivalents: `__forceinline` and `__declspec(noinline)`.

### The cost of too much inlining

Inlining duplicates code. A function inlined into 50 call sites exists 50 times. When the hot path's code outgrows the
32 KB L1 instruction cache (or the micro-op cache, about 1.5K micro-ops on Skylake), front-end stalls appear, and a
program can get *slower* from more inlining. This is part of why `-O3` isn't always faster than `-O2`. Watch the
hot path's code size (`size`, or `nm --size-sort`) and measure end-to-end, not just microbenchmarks, which almost
always fit in cache.

> [!TIP]
> A powerful pattern for hot functions with rare slow paths: keep the fast path small and inlinable, and move the
> slow path into a separate `noinline, cold` function. The fast path stays tiny at every call site.
>
> ```cpp
> inline void Book::add(const Order& o) {
>     if (count_ == capacity_) [[unlikely]] return grow_and_add(o);   // out-of-line, cold
>     orders_[count_++] = o;
> }
> ```

---

## Summary

- Inlining matters mostly for what it enables: constant propagation, vectorization, redundancy removal, and freedom
  from aliasing assumptions. Measured: an opaque call made a loop 6.7× slower.
- Compilers inline small, visible functions, favour calls in loops, and limit total growth.
- Obstacles: definitions in other TUs, indirect calls, unknown virtual targets, interposable symbols in shared
  libraries.
- Mark leaf classes `final` to enable devirtualization; use LTO for cross-file inlining.
- Check decisions with `-fopt-info-inline` / `-Rpass=inline` or the assembly. Steer with `always_inline`, `noinline`,
  `cold` and `flatten`, and split fast paths from slow paths.
- Too much inlining can overflow the instruction cache; measure whole-system performance.

## Key terms

**Inlining** · **inlining heuristics** · **call-site context** · **symbol interposition** · **devirtualization** ·
**speculative devirtualization** · **`final`** · **link-time optimization** · **`always_inline`** ·
**`noinline` / `cold`** · **`flatten`** · **instruction-cache pressure**
