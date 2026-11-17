+++
title = "Auto-Vectorization"
minutes = 45
practice_tags = ["numerics", "data-layout"]
+++

## Why this chapter matters

Five simple loops over 4,096 elements, measured on the test machine (Skylake, AVX2), in nanoseconds per element:

| Loop | `-O2` | `-O3 -march=native` | `-O3 -march=native -ffast-math` |
|---|---|---|---|
| sum of `int32_t` | 0.056 | 0.056 | 0.056 |
| count of `float`s above a threshold | 0.674 | **0.077** | 0.069 |
| simple returns `px[i] / px[i-1] - 1` | 1.334 | **0.674** | 0.671 |
| sum of `float`s | 1.313 | 1.313 | **0.149** |
| prefix sum `a[i] += a[i-1]` | 1.333 | 1.337 | 1.338 |

Same source code in every column. The differences, up to 24× between the slowest and fastest rows, come almost
entirely from whether the compiler turned the loop into **SIMD** code that processes 4, 8 or 32 elements per
instruction. The integer sum vectorized everywhere; the count needed `-O3`; the float sum needed permission to
reorder floating-point additions; and the prefix sum never vectorized, because each element depends on the previous
one.

Modern CPUs have vector registers 16, 32 or 64 bytes wide, and instructions that operate on all their lanes at once.
Getting the compiler to use them is often the cheapest big speedup available for numeric code. This chapter explains
what the compiler can vectorize, what stops it, and how to find out what it did.

By the end of this chapter you should be able to:

- explain SIMD, vector widths and lanes, and what `-march` has to do with them;
- predict whether a loop will auto-vectorize, and name the common obstacles;
- read the compiler's vectorization reports;
- restructure loops and data so they vectorize.

---

## 1. SIMD in one page

**SIMD** (Single Instruction, Multiple Data) instructions apply one operation to several values packed into a wide
register:

| Instruction set | Register width | `double`s per register | `float`s / `int32`s | Available on |
|---|---|---|---|---|
| SSE2 | 128 bits (`xmm`) | 2 | 4 | every x86-64 CPU |
| AVX / AVX2 | 256 bits (`ymm`) | 4 | 8 | Intel since 2013 (Haswell), AMD since 2015 |
| AVX-512 | 512 bits (`zmm`) | 8 | 16 | Intel server CPUs since 2017, AMD Zen 4+ |

On Skylake, a 256-bit vector add or multiply has the same latency and throughput as a scalar one (Chapter 5.1): two
per cycle, four cycles latency. So a loop that uses all 8 lanes of a `float` vector does 8× the arithmetic in the same
time, provided memory can keep up.

**`-march` decides what's available.** By default, GCC targets baseline x86-64, which only guarantees SSE2: 128-bit
vectors. To use AVX2 the compiler must be told the target CPU supports it: `-march=native` (the build machine),
`-march=x86-64-v3` (AVX2 + FMA, any CPU since about 2015), or a specific CPU like `-march=icelake-server`. Chapter 1.2's
warning applies: a binary built for AVX2 crashes with `SIGILL` on a CPU without it.

---

## 2. What the compiler can vectorize

The vectorizer turns a loop into one that processes several iterations at once, plus a scalar **remainder loop** (or
a masked final iteration) for leftover elements (Chapter 6.3 showed what this looks like in assembly). For that to be
legal and profitable, the loop must satisfy a few conditions.

**Iterations must be independent**, or the dependencies must be of a form the compiler understands:

- `out[i] = f(a[i], b[i])`: elementwise, always vectorizable (if `f` is).
- `s += a[i]`: a **reduction**. Vectorizable for integers by keeping several partial sums. For floating point, only
  if reassociation is allowed (section 3).
- `a[i] += a[i-1]`: a **loop-carried dependency** through memory. Each element needs the previous result: not
  vectorizable as written. (Parallel prefix-sum algorithms exist, but compilers don't apply them automatically.)

**The trip count must be computable** when the loop starts. Loops with a `break` that depends on the data
(`while (a[i] != target)`) generally don't vectorize, though GCC 14+ and Clang can vectorize some early-exit loops.

**The body must be simple enough**: no calls to functions that aren't inlined (Chapter 8.1), no complicated control
flow. Simple `if`s are usually fine, since the vectorizer turns them into masked operations or blends, exactly the
branchless techniques of Chapter 9.3. The `count_above` loop above is an example: `c += a[i] > t` becomes a vector
compare producing a mask, then an add.

**Memory access should be contiguous**. Unit-stride access (`a[i]`) vectorizes well. Strided (`a[2*i]`) or indirect
(`a[idx[i]]`) access needs gathers or shuffles, which may not be profitable, especially on AVX2 where gathers are
slow. Struct-of-arrays layouts (Chapter 7.2) exist largely for this reason.

**Pointers mustn't alias**, or the compiler must be able to check at run time that they don't (Chapter 8.2).

**The cost model must judge it profitable.** That's why `count_above` and `returns` stayed scalar at `-O2`: since
GCC 12, `-O2` enables the vectorizer with a "very cheap" cost model that only vectorizes when it needs no run-time
checks and no expensive epilogues. `-O3` uses a more permissive model. Clang vectorizes at `-O2` with its normal cost
model.

### Exercises

:::exercise Will it vectorize?
For each loop (all arrays `double`, no aliasing), say whether it can be auto-vectorized, and why: (a)
`y[i] = a * x[i] + y[i];` (b) `x[i] = x[i-1] * decay + p[i];` (c) `if (x[i] > 0) y[i] = std::sqrt(x[i]);` (d)
`y[i] = x[idx[i]];` (e) `s = std::max(s, x[i]);`
:::solution
(a) Yes: elementwise (this is the classic AXPY, ideal for FMA). (b) No: loop-carried dependency through `x[i-1]`, the
EMA recurrence from Chapter 5.1; vectorize across instruments instead. (c) Yes, usually: computed as a masked `sqrt`
and a blend or masked store (with `-fno-math-errno`, since `sqrt` of a negative sets `errno` otherwise; GCC handles it
because the branch excludes negatives). (d) Only with gathers: AVX2 has `vgatherdpd` but it's slow; the compiler may
or may not judge it profitable. (e) Yes for integers. For doubles, `max` isn't associative in the presence of NaN, so
GCC needs `-ffinite-math-only` or `-ffast-math`; written as `s = x[i] > s ? x[i] : s` it may vectorize with
`vmaxpd` semantics. Check the report.
:::

---

## 3. Floating-point reductions and `-ffast-math`

The float sum stayed at 1.3 ns per element with every optimization level: that's one add per four cycles, the latency
of the dependency chain through `s` (Chapter 5.1). To vectorize it, the compiler would keep 8 partial sums in a
vector register and add them at the end. That changes the order of additions, and floating-point addition isn't
associative, so the result could differ in the last bits. The compiler won't do it unless you allow it.

`-ffast-math` allows it, and the sum dropped to 0.149 ns per element: **8.8× faster**. But `-ffast-math` is a bundle of
permissions, several of them dangerous:

| Flag (part of `-ffast-math`) | Permits | Risk |
|---|---|---|
| `-fassociative-math` (with `-fno-signed-zeros -fno-trapping-math`) | reordering additions and multiplications | small result differences; can defeat compensated summation (Kahan) entirely |
| `-ffinite-math-only` | assuming no NaN or infinity | `std::isnan(x)` may be compiled to `false`; NaN checks on market data vanish |
| `-fno-math-errno` | math functions don't set `errno` | harmless for nearly all code; enables inlining `sqrt` and vectorized math |
| `-freciprocal-math` | `x / y` → `x * (1/y)` | small differences |
| `-fno-signed-zeros` | treating `-0.0` as `0.0` | rarely matters |

In finance code, `-ffinite-math-only` is the dangerous one: code that checks for NaN prices or failed calculations
may have those checks removed. Chapter 14.1 discusses this in depth.

Safer options:

- Enable only what you need: `-fno-math-errno` globally is almost always safe; `-fassociative-math` can be applied to
  one file or function (`[[gnu::optimize("associative-math")]]`, or `#pragma omp simd reduction(+:s)` with
  `-fopenmp-simd`).
- **Write the reduction with multiple accumulators yourself**, which makes the reordering explicit and portable,
  or use `std::reduce`, which permits reordering by contract (Chapter 4.3).
- Use intrinsics (Chapter 9.2): the hand-written AVX2 sum with four vector accumulators measured **0.045 ns per
  element**, 29× faster than the scalar loop, with fully deterministic results for a given input.

---

## 4. Asking the compiler what it did

Don't guess; ask:

```bash
g++ -O3 -march=native -fopt-info-vec-optimized file.cpp        # loops that were vectorized
g++ -O3 -march=native -fopt-info-vec-missed file.cpp           # loops that weren't, and why (verbose)
clang++ -O3 -march=native -Rpass=loop-vectorize -Rpass-missed=loop-vectorize -Rpass-analysis=loop-vectorize file.cpp
```

Typical GCC messages and what they mean:

| Message | Meaning |
|---|---|
| `loop vectorized using 32 byte vectors` | success, with AVX2 |
| `loop versioned for vectorization because of possible aliasing` | success, with a run-time overlap check (Chapter 8.2) |
| `reduction: unsafe fp math optimization` | a floating-point reduction needs reassociation (section 3) |
| `not vectorized: complicated access pattern` | strided, indirect or otherwise irregular memory access |
| `not vectorized: number of iterations cannot be computed` | data-dependent loop exit |
| `function call or data reference not analyzable` | an opaque call or an access the compiler can't model |
| `cost model: the vector iteration cost ... is divisible by...` / `not beneficial` | legal but judged unprofitable |

Then confirm in the assembly (Chapter 6.3): packed instructions on `ymm` registers in the loop body.

### Fixing loops that don't vectorize

| Obstacle | Fix |
|---|---|
| floating-point reduction | multiple accumulators, `std::reduce`, targeted `-fassociative-math`, or intrinsics |
| possible aliasing | locals, `__restrict`, separate input and output buffers |
| function call in the body | make it inlinable (header, LTO); `[[gnu::always_inline]]` |
| AoS layout, strided access | SoA layout (Chapter 7.2) |
| branchy body | branchless form (Chapter 9.3): `c += (x > t)` instead of `if (x > t) ++c` |
| data-dependent early exit | process in blocks: compute a vector of conditions for 8–32 elements, then check once |
| `-O2` cost model too cautious | `-O3` for that file or function, or `#pragma GCC ivdep` / `#pragma omp simd` to assert independence |
| mixed types (`int` index into `double` math with conversions) | keep types uniform; `int32` vs `int64` widening costs lanes |

### Exercises

:::exercise Make it vectorize
This loop computes, for each tick, whether the price moved up. It doesn't vectorize at `-O3`. Why, and how would you
fix it?

```cpp
int count_upticks(const std::vector<Tick>& ticks) {      // struct Tick { std::int64_t ts; double px; int qty; };
    int n = 0;
    for (std::size_t i = 1; i < ticks.size(); ++i)
        if (ticks[i].px > ticks[i - 1].px) ++n;
    return n;
}
```
:::solution
The `px` values are strided: each `Tick` is 24 bytes, so consecutive prices aren't contiguous, and the compiler would
need gathers or shuffles to build vectors of prices (it may judge that unprofitable). The comparison of neighbours is
fine (it's two loads, not a dependency on a computed result). Fix: keep prices in their own `std::vector<double>`
(SoA), and write the body branchlessly: `n += px[i] > px[i - 1];`. That vectorizes into loads of `px[i]` and
`px[i-1]` (offset by one element), a packed compare, and a masked add.
:::

---

## Summary

- SIMD registers hold 2–16 values; one instruction processes all lanes. AVX2 (256-bit) needs `-march=x86-64-v3` or
  newer; the default target only has SSE2.
- Vectorizable loops have independent iterations (or recognized reductions), computable trip counts, inlinable
  bodies, contiguous access and no unresolved aliasing.
- GCC's `-O2` cost model vectorizes only very cheap cases; `-O3` and Clang vectorize more. Measured: a count loop went
  from 0.67 to 0.077 ns per element.
- Floating-point reductions need permission to reassociate. `-ffast-math` gave 8.8× on a float sum but brings
  dangerous assumptions (`-ffinite-math-only`); prefer targeted flags, multiple accumulators or intrinsics.
- Use `-fopt-info-vec` / `-Rpass` reports and the assembly to see what happened, and restructure loops and data to
  remove obstacles.

## Key terms

**SIMD** · **vector register** · **lane** · **SSE / AVX2 / AVX-512** · **`-march`** · **auto-vectorization** ·
**remainder loop** · **reduction** · **loop-carried dependency** · **cost model** · **reassociation** ·
**`-ffast-math`** · **vectorization report** · **gather**
