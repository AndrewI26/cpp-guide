+++
title = "SIMD Kernels for Finance"
minutes = 45
practice_tags = ["numerics", "finance", "data-layout"]
+++

## Why this chapter matters

The last three chapters gave you the tools: auto-vectorization, intrinsics and branch-free code. This chapter
applies them to computations that appear in every quant codebase (returns, moving averages, exponential averages
and option pricing) and measures the results on the test machine:

| Kernel | Straightforward version | After restructuring | Speedup |
|---|---|---|---|
| 50-tick simple moving average | 31.5 ns/output (`-O2`) | 1.49 ns/output (running sum) | 21× |
| EMA of 512 instruments × 2,048 ticks | 3.06 ns/update (one instrument at a time) | 0.36–0.41 ns/update (all instruments per tick) | 7.5–8.5× |
| log returns | 7.9 ns/element | 2.7 ns/element (vectorized `log`) | 2.9× |
| Black–Scholes call price | 139 ns/option | 17.4 ns/option (vectorized `exp`/`log`/`erfc`) | 8× |

Every one of these came from one of three ideas: **change the algorithm** so there's less work, **change the loop
order** so independent work sits in adjacent lanes, or **give the compiler vector versions of the math functions**.
None needed hand-written intrinsics.

By the end of this chapter you should be able to:

- turn windowed computations into incremental ones;
- vectorize recurrences across instruments instead of across time;
- get vectorized transcendental functions (`exp`, `log`, `erfc`) safely;
- structure a pricing kernel for SIMD: SoA inputs, branch-free bodies, verified accuracy.

---

## 1. Moving averages: fix the algorithm first

A 50-tick simple moving average computed directly sums 50 values per output:

```cpp
void sma_naive(const double* x, double* out, std::size_t n, std::size_t w) {
    for (std::size_t i = w - 1; i < n; ++i) {
        double s = 0;
        for (std::size_t k = 0; k < w; ++k) s += x[i + 1 - w + k];
        out[i] = s / w;
    }
}
```

At `-O2` this takes 31.5 ns per output; at `-O3 -march=native` the compiler vectorizes the loop nest and it reaches
8.8 ns. But the
real fix is algorithmic: a **running sum** adds the new value and subtracts the one leaving the window, O(1) per
output instead of O(w):

```cpp
void sma_running(const double* x, double* out, std::size_t n, std::size_t w) {
    double s = 0;
    for (std::size_t i = 0; i < w; ++i) s += x[i];
    out[w - 1] = s / w;
    for (std::size_t i = w; i < n; ++i) {
        s += x[i] - x[i - w];
        out[i] = s / w;
    }
}
```

1.49 ns per output: 21× faster than the naive `-O2` version, and still 6× faster than the vectorized naive one. The
running-sum loop is itself a dependency chain through `s` (4 cycles per add, Chapter 5.1), which is exactly its
1.49 ns. It doesn't vectorize, but it doesn't need to.

> [!QUANT]
> Running sums accumulate floating-point error over long series: each add and subtract rounds, and the error never
> cancels. Production code periodically recomputes the window sum from scratch, uses compensated (Kahan) updates, or
> keeps prices as integer ticks so the running sum is exact. Chapter 14.3 and the practice problems "O(1) Simple
> Moving Average" and "Compensated Summation" cover this.

---

## 2. Recurrences: vectorize across instruments

An exponential moving average is a recurrence, `e = a·p + (1 − a)·e`, so each step depends on the previous one and
can't be vectorized along time (Chapters 5.1 and 9.1). But trading systems rarely compute one EMA; they compute one per
instrument, and different instruments are independent.

**Instrument-major** (each instrument's whole history, one after another):

```cpp
for (std::size_t i = 0; i < I; ++i) {                 // px laid out [instrument][time]
    double v = px[i * T];
    for (std::size_t t = 1; t < T; ++t) v = a * px[i * T + t] + (1 - a) * v;   // a dependency chain
    e[i] = v;
}
```

3.06 ns per update: each step puts a multiply and an add on the chain, 4 cycles each, so about 8 cycles or 3 ns at
the test machine's clock (Chapter 5.1).

**Time-major** (each tick, update every instrument):

```cpp
for (std::size_t t = 1; t < T; ++t)                   // px laid out [time][instrument]
    for (std::size_t i = 0; i < I; ++i)
        e[i] = a * px[t * I + i] + (1 - a) * e[i];    // independent across i: vectorizes
```

0.72 ns per update at `-O2` (scalar, but the core overlaps independent instruments), 0.41 ns at `-O3 -march=native`
(4 instruments per AVX2 instruction), 0.36 ns with `-ffast-math`. The arithmetic is identical; the loop order puts
independent work into adjacent lanes. That's also how live systems naturally receive data: one tick across many
instruments at a time. Store per-instrument state as SoA (Chapter 7.2): an array of EMAs, an array of variances, and
so on, indexed by a dense instrument ID.

### Exercises

:::exercise Many volatilities
You maintain an EWMA variance `v = λ·v + (1 − λ)·r²` for 2,000 instruments, updated once per second from each
instrument's latest return. How should you lay out the data and write the update so it vectorizes?
:::solution
Keep `std::vector<double> var(2000)` and `std::vector<double> ret(2000)` (SoA, indexed by instrument ID), and update
all of them in one loop each second: `for (i...) var[i] = lambda * var[i] + (1 - lambda) * ret[i] * ret[i];`. The
iterations are independent, contiguous and branch-free, so it vectorizes to 4 instruments per AVX2 FMA (8 with
AVX-512). Storing a `struct InstrumentStats { double var, ema, last_px, ...; }` per instrument (AoS) would make the
`var` values strided and the loop harder to vectorize.
:::

---

## 3. Vectorized math functions

`std::log`, `std::exp` and `std::erfc` are library calls, and a loop containing a library call can't be vectorized
unless a vector version of the function exists. glibc ships one: **libmvec**, with AVX2 and AVX-512 versions of
`exp`, `log`, `pow`, `sin`, `cos`, `erf`, `erfc` and others, each computing 4 or 8 results per call.

GCC uses libmvec automatically, but only when `__FAST_MATH__` is defined: glibc's headers only declare the vector
variants under `-ffast-math`. Without it, the log-returns loop runs at 7.9 ns per element, calling scalar `log` once
per element. With `-ffast-math`, GCC calls `_ZGVdN4v_log` (the 4-wide AVX2 `log`), and it drops to **2.7 ns**.

`-ffast-math` brings dangers (Chapter 9.1): in particular `-ffinite-math-only` lets the compiler remove NaN and
infinity checks. Contain it:

- **Put vectorized math kernels in their own source file** compiled with `-ffast-math`, with inputs validated
  before they reach it (no NaNs, positive prices, positive volatilities), and the rest of the program compiled
  normally.
- **Or use a vector math library explicitly**: SLEEF, Intel's SVML/oneMKL VM, or the math functions in Highway, xsimd
  or EVE give vectorized `exp`/`log` without global flags.
- **Check accuracy** against a high-precision reference (below).

```cpp
// pricing_kernels.cpp: compiled with -O3 -march=x86-64-v3 -ffast-math
void log_returns(const double* __restrict px, double* __restrict r, std::size_t n) {
    for (std::size_t i = 1; i < n; ++i) r[i] = std::log(px[i] / px[i - 1]);
}
```

---

## 4. A vectorized Black–Scholes kernel

Pricing a book of European options is a perfect SIMD workload: thousands of independent evaluations of the same
formula. The kernel needs three things to vectorize: SoA inputs, a branch-free body, and vector math functions.

```cpp
inline double norm_cdf(double x) { return 0.5 * std::erfc(-x * M_SQRT1_2); }   // accurate in both tails

void bs_call(const double* __restrict S, const double* __restrict K, const double* __restrict T,
             const double* __restrict vol, double r, double* __restrict out, std::size_t n) {
    for (std::size_t i = 0; i < n; ++i) {
        double sq = vol[i] * std::sqrt(T[i]);
        double d1 = (std::log(S[i] / K[i]) + (r + 0.5 * vol[i] * vol[i]) * T[i]) / sq;
        double d2 = d1 - sq;
        out[i] = S[i] * norm_cdf(d1) - K[i] * std::exp(-r * T[i]) * norm_cdf(d2);
    }
}
```

Measured over 16,384 options:

| Build | ns per option | Max absolute error vs `long double` reference |
|---|---|---|
| `-O3 -march=native` | 139 | 2.6 × 10⁻¹⁴ |
| `-O3 -march=native -ffast-math` (libmvec) | 17.4 | 3.6 × 10⁻¹⁴ |

8× faster, with errors around 10⁻¹⁴ on prices of order 1–20, far below anything that matters for a price (and
comparable to the scalar library's own error). Verifying accuracy is part of the job: compute a reference in higher
precision (`long double`, or a multiprecision library) over a grid of inputs covering the realistic range, including
deep in- and out-of-the-money options and short expiries, and check the maximum error.

Further steps for a production pricer:

- **Greeks in the same pass**: delta, gamma, vega and theta share `d1`, `d2`, the CDF and PDF values; computing them
  together reuses the expensive transcendental calls.
- **`float` where precision allows** doubles the lanes; check the error budget first (Chapter 14.1).
- **Branch-free handling of edge cases**: expiry at zero, zero volatility. Clamp inputs (`std::max(T, tiny)`) instead
  of branching per option, and fix up the rare exact cases afterwards.
- **Implied volatility** is a root-finding problem per option (Chapter 14.5); vectorize across options by running
  Newton iterations on all of them in lock-step, masking out converged lanes.

The option-pricer project builds this end to end, including Greeks, implied volatility and a Monte Carlo check.

---

## Summary

- Fix the algorithm before vectorizing: a running-sum SMA was 21× faster than the naive window sum.
- Recurrences can't be vectorized along time; vectorize across instruments with time-major loops and SoA state: 7.5–8.5×
  for an EMA over 512 instruments.
- Transcendental functions need vector implementations. glibc's libmvec provides them to GCC under `-ffast-math`;
  confine that flag to validated kernel files, or use SLEEF/Highway-style libraries.
- A Black–Scholes kernel with SoA inputs, a branch-free body and vector math ran 8× faster with errors ~10⁻¹⁴.
- Always verify vectorized numerics against a high-precision reference over the full input range.

## Key terms

**Running sum** · **incremental computation** · **time-major vs instrument-major** · **libmvec** ·
**vector math library** · **SLEEF** · **accuracy verification** · **lock-step iteration**
