+++
title = "Branchless Programming"
minutes = 45
practice_tags = ["branchless", "algorithms", "cache"]
+++

## Why this chapter matters

Binary search over a sorted array of prices, one million random lookups on the test machine:

| Array size | `std::lower_bound` | branch-free search | branch-free + prefetch |
|---|---|---|---|
| 1,000 elements (8 KB, in L1) | 51.0 ns | **15.0 ns** | 17.2 ns |
| 100,000 elements (780 KB, L2/L3) | 117.9 ns | 52.7 ns | **46.5 ns** |
| 10,000,000 elements (78 MB, DRAM) | 416.2 ns | 466.9 ns | **293.9 ns** |

In cache, removing the branch makes the search **3.4× faster**: each step of a binary search goes left or right at
random, so `std::lower_bound` mispredicts about half its steps (Chapter 5.4). But from DRAM, the branch-free version is
*slower*, and only becomes faster than `std::lower_bound` once prefetching is added. And getting this table at all
required a detour: the first "branchless" version, written with a ternary operator, was compiled by GCC into a
branch, and ran no faster than `std::lower_bound`.

Branchless programming means computing results with arithmetic, masks and conditional moves instead of jumps. It's
the scalar counterpart of SIMD (every vector `if` is a mask), and it's how you deal with unpredictable data. This
chapter covers the techniques, how to make sure the compiler keeps your code branch-free, and when branches are
actually better.

By the end of this chapter you should be able to:

- convert conditionals into selects, masks and arithmetic;
- verify that the compiler kept code branch-free, and force it when it didn't;
- explain the speculation trade-off: why branchy code can win when the branch hides memory latency;
- write branch-free versions of common kernels: min/max/clamp, conditional accumulation, partitioning and binary
  search.

---

## 1. The tools

**Selects (conditional moves).** `x = c ? a : b` can compile to `cmov`, which picks a value based on a flag without
jumping. Both `a` and `b` must be computed (or already available) first.

**Comparisons as integers.** In C++, a comparison yields a `bool` that converts to 0 or 1:

```cpp
count += (px > threshold);                 // conditional increment
total += qty * (side == Side::Buy);        // conditional accumulate
int sign = (d > 0) - (d < 0);              // -1, 0, +1
```

**Masks.** Negating a 0/1 value gives all-zeros or all-ones, which selects with bitwise AND:

```cpp
std::int64_t mask = -static_cast<std::int64_t>(c);   // c ? ~0 : 0
std::int64_t r = (a & mask) | (b & ~mask);           // c ? a : b
```

This is exactly what SIMD comparisons produce per lane (Chapter 9.2), so code written this way often vectorizes as a
bonus.

**Min, max and clamp.** `std::min`, `std::max` and `std::clamp` on integers and floating point compile to `cmov` or
`minsd`/`maxsd`. Use them rather than writing `if`s.

**Index arithmetic instead of control flow.** Advance a pointer or index by a computed amount:
`out += keep;` writes an element unconditionally, then only moves the output position if it's kept (see section 3).

**Table lookups.** Replace a chain of conditions on a small input with a precomputed table (Chapter 3.3).

---

## 2. Keeping the compiler honest

The compiler decides whether `c ? a : b` becomes a `cmov` or a branch, using heuristics about predictability and
cost. It's free to turn your "branchless" code into a branch, as happened with this binary search:

```cpp
base = (base[half - 1] < key) ? base + half : base;     // GCC 15 -O2: cmp + jle (a branch)
```

and equally free to turn an `if` into a `cmov` (as Chapter 5.4's first benchmark discovered). So:

1. **Check the assembly** (Chapter 6.3) for every branch-free kernel: look for `cmov`, `set`, or SIMD blends, and the
   absence of conditional jumps in the hot loop.
2. **When the compiler insists on a branch, use arithmetic** that has no branch to recover. Multiplying by the
   comparison result did it here:

   ```cpp
   base += (base[half - 1] < key) * half;               // compiled to setcc/cmov + add: no branch
   ```

3. **Compiler-specific tools** exist: `__builtin_expect_with_probability(c, 1, 0.5)` tells GCC the branch is
   unpredictable, which encourages `cmov`; Clang has `__builtin_unpredictable(c)`.
4. **Re-check after compiler upgrades.** The decision can change between versions.

> [!WARNING]
> Some branch-free idioms are slower than they look. Integer multiplication by a 0/1 value is cheap (3 cycles), but
> data-dependent divisions, or "computing both sides" when one side is expensive (a function call, a cache miss),
> can cost more than an occasional mispredict.

### Exercises

:::exercise Branch-free clamp and select
Write branch-free versions of: (a) `int64_t clamp_qty(int64_t q, int64_t lo, int64_t hi)`; (b) picking the bid
or ask quantity depending on a side flag: `side == Side::Buy ? bid_qty : ask_qty`; (c) counting how many
elements of an array fall inside `[lo, hi]`.
:::solution
(a) `return std::clamp(q, lo, hi);` (compiles to two `cmov`s). (b) `return side == Side::Buy ? bid_qty : ask_qty;`
usually compiles to `cmov` because both values are already in registers; if it doesn't, use a mask:
`auto m = -std::int64_t(side == Side::Buy); return (bid_qty & m) | (ask_qty & ~m);`. (c)
`for (auto x : a) n += (x >= lo) & (x <= hi);`. Note the `&` rather than `&&`: `&&` short-circuits, which is a
branch; `&` evaluates both comparisons. The loop also vectorizes.
:::

---

## 3. Branch-free partitioning and filtering

Copying the elements that satisfy a condition (a filter) is normally:

```cpp
for (std::size_t i = 0; i < n; ++i)
    if (orders[i].qty > 0) out[k++] = orders[i];      // unpredictable if fills and non-fills are mixed
```

The branch-free version always writes, and only advances the output index when the element is kept:

```cpp
for (std::size_t i = 0; i < n; ++i) {
    out[k] = orders[i];                                // unconditional store
    k += (orders[i].qty > 0);                          // advance by 0 or 1
}
```

The output buffer must have room for one extra element (the last write may be discarded), and every element is
written, which costs bandwidth for large elements. For small elements and unpredictable conditions, it's typically
several times faster. The same technique is the core of branch-free quicksort partitioning (as used in pdqsort's
"block partition"), and with AVX-512's compress instruction, it becomes a single vector operation per 8–16 elements.

---

## 4. The speculation trade-off

Why was the branch-free binary search slower than `std::lower_bound` once the array was in DRAM?

A **branchy** search predicts each step. Even when the prediction is wrong half the time, the CPU **speculatively
starts loading** the next probe along the predicted path immediately, overlapping that load with the current one. When
it's right, it got the next cache miss started a whole DRAM latency early. That's a form of prefetching, done by the
branch predictor.

A **branch-free** search has a true data dependency: the next address is computed from the comparison result, which
needs the current load to finish. Each step waits a full memory latency, about 100 ns × log₂(n) for a large array. No
speculation, no overlap.

So:

- In cache, where loads are cheap, mispredictions dominate, and branch-free wins (3.4×).
- From DRAM, memory latency dominates, and the branchy version's accidental prefetching wins.
- **Branch-free plus explicit prefetching** gets both: at each step, prefetch both possible next probes (one in each
  half), so whichever way the comparison goes, its line is already on the way:

  ```cpp
  while (n > 1) {
      std::size_t half = n / 2;
      __builtin_prefetch(base + half / 2 - 1);           // next probe if we go left
      __builtin_prefetch(base + half + half / 2 - 1);    // next probe if we go right
      base += (base[half - 1] < key) * half;
      n -= half;
  }
  ```

  That's 294 ns versus 416 ns for `std::lower_bound` on the 78 MB array: 1.4× faster. It's one of the few places where
  software prefetch clearly pays (compare Chapter 8.5): the hardware can't know the next address, but you know both
  candidates.

This is the general lesson: **branches aren't only a cost; they're also speculation.** Removing a branch that guards
a memory access can remove useful speculative work. Measure at realistic sizes.

> [!NOTE]
> For large sorted arrays that are searched often, the layout matters even more than the code: the **Eytzinger** (BFS)
> layout stores the implicit binary tree level by level, so the first few levels share cache lines and the next probes
> are adjacent, making prefetching even more effective. For order books, bitmaps of active price levels (the practice
> problem "Find the Next Price Level with a Bitmap") avoid searching altogether.

### Exercises

:::exercise Predict the winner
Which version, branchy or branch-free, would you expect to win in each case? (a) Clamping each of 10 million
random quantities to `[0, max_qty]`, where 0.1% are out of range. (b) The same, where 40% are out of range at random.
(c) Searching a 50 KB sorted array of price levels per message.
:::solution
(a) Branchy, or equal: the branch is 99.9% predictable, so it's nearly free, and the compiler will likely emit `cmov`
for `std::clamp` anyway. (b) Branch-free: a random 40% mispredicts constantly. (c) Branch-free: 50 KB fits in L2, so
the search is limited by mispredictions rather than memory latency, like the 8 KB and 780 KB rows of the table.
:::

---

## Summary

- Branch-free code computes results with selects, 0/1 arithmetic and masks instead of jumps. It can't mispredict.
- Measured: a branch-free binary search was 3.4× faster than `std::lower_bound` in L1.
- The compiler may turn a ternary into a branch (it did here) or an `if` into a `cmov`. Check the assembly; force it
  with arithmetic when needed.
- Branch-free filtering writes every element and advances the output index by the condition.
- Branches are also speculation: from DRAM, the branchy search won by prefetching accidentally. Branch-free plus
  explicit prefetching of both candidates won overall (1.4× over `std::lower_bound`).
- Use branch-free code for unpredictable conditions on cached data, and measure at realistic sizes.

## Key terms

**Branch-free code** · **select / conditional move** · **mask** · **0/1 arithmetic** · **short-circuit evaluation** ·
**branch-free filter** · **speculation** · **data dependency** · **Eytzinger layout**
