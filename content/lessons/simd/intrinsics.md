+++
title = "Intrinsics: SSE, AVX2 and AVX-512"
minutes = 50
practice_tags = ["bit-manipulation", "numerics"]
+++

## Why this chapter matters

Chapter 9.1 ended with a floating-point sum that the compiler refused to vectorize without `-ffast-math`: 1.31 ns
per element. Written by hand with AVX2 **intrinsics**, using four vector accumulators, the same sum runs at
**0.045 ns per element** on the test machine, 29× faster, with no global flags and a deterministic result. A
threshold count written with intrinsics matched the best auto-vectorized code exactly (0.077 ns per element), which
is also worth knowing: when the compiler *does* vectorize, hand-written code often isn't faster.

Intrinsics are C/C++ functions that map almost one-to-one onto SIMD instructions. They let you write vector code
explicitly when the compiler can't or won't: reductions it won't reorder, algorithms that need shuffles or masks it
doesn't discover, or code where you need guaranteed vectorization regardless of compiler version. This chapter
teaches enough of the AVX2 intrinsics vocabulary to write real kernels, and covers AVX-512 and how to ship code that
uses whichever instruction set the CPU supports.

By the end of this chapter you should be able to:

- read intrinsic names and know the main families: load/store, arithmetic, compare, blend, shuffle, convert;
- write vectorized loops with intrinsics, including remainders and horizontal reductions;
- use comparison masks with `movemask` and bit tricks to search data;
- explain what AVX-512 adds, and dispatch between instruction sets at run time.

---

## 1. Types and naming

Include `<immintrin.h>` and compile with the right `-m` or `-march` flags (`-mavx2 -mfma`, or `-march=x86-64-v3`).

The vector types:

| Type | Width | Holds |
|---|---|---|
| `__m128`, `__m128d`, `__m128i` | 128 bits | 4 `float` / 2 `double` / integers of any width |
| `__m256`, `__m256d`, `__m256i` | 256 bits | 8 `float` / 4 `double` / integers |
| `__m512`, `__m512d`, `__m512i` | 512 bits | 16 `float` / 8 `double` / integers |

Intrinsic names follow a pattern: `_mm<width>_<operation>_<type>`.

- Width prefix: `_mm_` (128-bit), `_mm256_`, `_mm512_`.
- Type suffix: `ps` (packed single, `float`), `pd` (packed double), `epi32` / `epi64` / `epi8` (packed signed
  integers of that width), `epu8` etc. (unsigned), `si256` (the whole register as bits), `ss` / `sd` (scalar, lowest
  lane only).

So `_mm256_add_pd` adds four pairs of `double`s; `_mm256_cmpgt_epi32` compares eight pairs of 32-bit integers;
`_mm256_loadu_si256` loads 256 bits of integer data from an unaligned address. Intel's online **Intrinsics Guide** lists
every one, with latency and throughput per CPU; keep it open while writing SIMD code.

### The core vocabulary

| Category | Examples (AVX2) | Notes |
|---|---|---|
| load / store | `_mm256_loadu_pd`, `_mm256_storeu_pd`, `_mm256_load_pd` | `u` = unaligned (same speed when data is aligned; use it by default) |
| broadcast / set | `_mm256_set1_pd(x)`, `_mm256_setzero_pd()`, `_mm256_set_pd(d, c, b, a)` | `set` lists lanes from highest to lowest |
| arithmetic | `_mm256_add_pd`, `_mm256_mul_pd`, `_mm256_div_pd`, `_mm256_fmadd_pd`, `_mm256_max_pd` | FMA: `a*b + c` in one instruction, one rounding |
| compare | `_mm256_cmp_pd(a, b, _CMP_GT_OQ)`, `_mm256_cmpeq_epi8` | result is a **mask**: all-ones or all-zeros per lane |
| logic / blend | `_mm256_and_pd`, `_mm256_blendv_pd(a, b, mask)` | select per lane using a mask: branchless |
| mask to bits | `_mm256_movemask_pd`, `_mm256_movemask_epi8` | one bit per lane into a scalar `int` |
| shuffle / permute | `_mm256_permute4x64_pd`, `_mm256_shuffle_ps`, `_mm256_unpacklo_pd` | rearranging lanes; often the hard part |
| convert | `_mm256_cvtepi32_pd`, `_mm256_cvtpd_ps` | between types and widths |

---

## 2. A first kernel: counting with masks

Count how many prices are above a threshold:

```cpp
#include <immintrin.h>

int count_above_avx2(const float* a, int n, float t) {
    const __m256 vt = _mm256_set1_ps(t);           // t in all 8 lanes
    __m256i acc = _mm256_setzero_si256();          // 8 lane counters
    int i = 0;
    for (; i + 8 <= n; i += 8) {
        __m256 v = _mm256_loadu_ps(a + i);
        __m256 m = _mm256_cmp_ps(v, vt, _CMP_GT_OQ);           // lanes > t become all-ones (= -1 as int32)
        acc = _mm256_sub_epi32(acc, _mm256_castps_si256(m));   // subtracting -1 adds 1
    }
    alignas(32) int lanes[8];
    _mm256_store_si256(reinterpret_cast<__m256i*>(lanes), acc);
    int c = lanes[0] + lanes[1] + lanes[2] + lanes[3] + lanes[4] + lanes[5] + lanes[6] + lanes[7];
    for (; i < n; ++i) c += a[i] > t;              // scalar remainder
    return c;
}
```

Three patterns appear in nearly every SIMD kernel:

1. **Broadcast constants** into a vector outside the loop.
2. **Masks as values**: a comparison produces all-ones lanes where true. Treated as integers they're -1, so
   subtracting the mask counts; ANDing data with a mask zeroes the false lanes; `blendv` selects between two vectors.
3. **A horizontal reduction and a scalar remainder** after the main loop. (Reducing through memory, as here, is
   simple and fine outside the hot loop.)

`_mm256_castps_si256` reinterprets bits without any instruction; it just changes the type for the compiler.

---

## 3. Reductions done right

Here is the float sum that beat the scalar loop by 29×:

```cpp
float sum_avx2(const float* a, int n) {
    __m256 s0 = _mm256_setzero_ps(), s1 = s0, s2 = s0, s3 = s0;   // 4 independent accumulators
    int i = 0;
    for (; i + 32 <= n; i += 32) {
        s0 = _mm256_add_ps(s0, _mm256_loadu_ps(a + i));
        s1 = _mm256_add_ps(s1, _mm256_loadu_ps(a + i + 8));
        s2 = _mm256_add_ps(s2, _mm256_loadu_ps(a + i + 16));
        s3 = _mm256_add_ps(s3, _mm256_loadu_ps(a + i + 24));
    }
    __m256 s = _mm256_add_ps(_mm256_add_ps(s0, s1), _mm256_add_ps(s2, s3));
    __m128 lo = _mm256_castps256_ps128(s);          // lanes 0-3 (no instruction)
    __m128 hi = _mm256_extractf128_ps(s, 1);        // lanes 4-7
    lo = _mm_add_ps(lo, hi);                        // 4 partial sums
    lo = _mm_hadd_ps(lo, lo);                       // 2
    lo = _mm_hadd_ps(lo, lo);                       // 1
    float r = _mm_cvtss_f32(lo);
    for (; i < n; ++i) r += a[i];
    return r;
}
```

Why **four** vector accumulators and not one? The same reason as Chapter 5.1's `sum4`: a vector add still has 4
cycles of latency, so one accumulator would be a dependency chain processing 8 floats per 4 cycles. Four independent
chains keep both FP units busy: 32 floats in flight. Vectorization and breaking dependency chains multiply.

The result differs from the scalar loop's in the last bits (the additions happen in a different order), but it's
**deterministic**: the same input always gives the same output on any AVX2 machine. That's often what finance code
actually needs, rather than bit-identical agreement with a scalar loop.

### Exercises

:::exercise Dot product
Write `double dot(const double* a, const double* b, int n)` with AVX2 and FMA, using two accumulators.
:::solution
```cpp
double dot(const double* a, const double* b, int n) {
    __m256d s0 = _mm256_setzero_pd(), s1 = _mm256_setzero_pd();
    int i = 0;
    for (; i + 8 <= n; i += 8) {
        s0 = _mm256_fmadd_pd(_mm256_loadu_pd(a + i),     _mm256_loadu_pd(b + i),     s0);
        s1 = _mm256_fmadd_pd(_mm256_loadu_pd(a + i + 4), _mm256_loadu_pd(b + i + 4), s1);
    }
    __m256d s = _mm256_add_pd(s0, s1);
    __m128d lo = _mm_add_pd(_mm256_castpd256_pd128(s), _mm256_extractf128_pd(s, 1));
    double r = _mm_cvtsd_f64(_mm_add_sd(lo, _mm_unpackhi_pd(lo, lo)));
    for (; i < n; ++i) r += a[i] * b[i];
    return r;
}
```
Two accumulators give 2 × 4 = 8 doubles in flight; with FMA's 4-cycle latency and two FMA units, up to 8 chains can
be useful, so for long arrays four accumulators may be faster still. Measure.
:::

---

## 4. Searching with `movemask`

`movemask` turns a vector mask into an ordinary integer with one bit per lane, so you can branch once for a whole
vector and use bit operations to find which lanes matched:

```cpp
// Index of the first price >= target in an ascending array, or n if none (a linear search; Chapter 9.3 covers
// branchless binary search)
int find_first_ge(const double* px, int n, double target) {
    const __m256d vt = _mm256_set1_pd(target);
    int i = 0;
    for (; i + 4 <= n; i += 4) {
        __m256d m = _mm256_cmp_pd(_mm256_loadu_pd(px + i), vt, _CMP_GE_OQ);
        int bits = _mm256_movemask_pd(m);            // bit k set if lane k matched
        if (bits) return i + std::countr_zero(static_cast<unsigned>(bits));   // lowest set bit = first match
    }
    for (; i < n; ++i) if (px[i] >= target) return i;
    return n;
}
```

The same pattern, with `_mm256_cmpeq_epi8` and `_mm256_movemask_epi8` on 32 bytes at a time, is how fast `memchr`,
`strlen` and SIMD parsers find delimiters: one compare and one branch per 32 bytes instead of per byte. A FIX parser
looking for the SOH separator uses exactly this (Chapter 13.2).

---

## 5. AVX-512 and masking

AVX-512 doubles the width again (8 doubles, 16 floats per register) and adds features that make SIMD code simpler:

- **Mask registers** `k0`–`k7`: comparisons produce a compact bit mask, and almost every instruction can take a mask
  to apply only to selected lanes:

  ```cpp
  __mmask8 m = _mm512_cmp_pd_mask(v, vt, _CMP_GT_OQ);
  __m512d r = _mm512_mask_add_pd(old, m, a, b);       // lanes where m is set: a + b; others keep old
  ```

- **Masked loads and stores** handle the remainder without a scalar loop: load the last `n % 8` elements with a mask
  `(1u << rem) - 1`.
- More instructions: compress/expand (pack selected lanes together), conflict detection, ternary logic, better
  gathers and scatters.

Caveats: AVX-512 isn't available on many desktop CPUs (including the test machine), and on some older Intel server
CPUs heavy 512-bit use lowers the core's clock frequency, which can make mixed workloads slower overall. Measure on
the deployment hardware.

---

## 6. Shipping SIMD code

Intrinsics for an instruction set the CPU lacks crash with `SIGILL`. Options:

- **Compile for a known baseline** (`-march=x86-64-v3` for AVX2) when you control the production hardware, which is
  the normal case in trading.
- **Function multiversioning**: compile several versions and pick at load time:

  ```cpp
  [[gnu::target_clones("avx2", "default")]]
  float sum(const float* a, int n) { /* plain C++: the compiler vectorizes each clone for its target */ }
  ```

- **Manual dispatch**: compile kernels in separate files with different flags, and choose with
  `__builtin_cpu_supports("avx2")` at start-up, storing a function pointer.
- **Portable SIMD libraries**: Google Highway, xsimd, EVE, or C++26's `std::simd` (available as
  `std::experimental::simd` in GCC since version 11) let you write width-agnostic vector code once.

> [!TIP]
> Always keep a plain scalar version of every SIMD kernel, and test the SIMD version against it on random inputs,
> including lengths that aren't multiples of the vector width (0, 1, 7, 9, 31, 33...). Remainder handling is where most
> SIMD bugs live.

---

## Summary

- Intrinsics map directly onto SIMD instructions: `_mm256_add_pd` and friends, on `__m256`/`__m256d`/`__m256i` types.
- Core patterns: broadcast constants, masks from comparisons used as values (count, AND, blend), horizontal
  reductions and scalar remainders.
- Use several vector accumulators to hide latency: the AVX2 float sum was 29× faster than scalar.
- `movemask` + `countr_zero` finds matches across a whole vector with one branch.
- AVX-512 adds mask registers and masked operations, but isn't universal and can lower clock speeds on older CPUs.
- Ship with a pinned `-march`, multiversioning or run-time dispatch, and always test against a scalar reference.

## Key terms

**Intrinsic** · **`__m256d` / `__m256i`** · **packed / scalar** · **broadcast** · **FMA** · **comparison mask** ·
**blend** · **`movemask`** · **horizontal reduction** · **shuffle / permute** · **AVX-512 mask register** ·
**function multiversioning** · **`std::simd`**
