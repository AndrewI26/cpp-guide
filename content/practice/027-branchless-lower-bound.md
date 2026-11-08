+++
title = "Branchless Binary Search"
difficulty = "medium"
tags = ["branchless", "algorithms", "cache"]
+++

Binary search over a sorted array of price levels is a hot operation. The textbook version branches on every
comparison, and since the comparison result is essentially random, the CPU **mispredicts about half of those
branches**, at roughly 15 cycles each.

Implement `lower_bound` (the index of the first element ≥ `key`, or `n` if none) with **no data-dependent
branches in the loop**:

```cpp
std::size_t branchless_lower_bound(std::span<const std::int64_t> a, std::int64_t key);
```

## Starter Code

```cpp
#pragma once
#include <cstddef>
#include <cstdint>
#include <span>

inline std::size_t branchless_lower_bound(std::span<const std::int64_t> a, std::int64_t key) {
    std::size_t lo = 0, hi = a.size();
    while (lo < hi) {                       // classic version: one unpredictable branch per step
        std::size_t mid = lo + (hi - lo) / 2;
        if (a[mid] < key) lo = mid + 1; else hi = mid;
    }
    return lo;
}
```

## Hints

- Keep a `base` index and a remaining `len`. Each step halves `len` and *conditionally* advances `base`:
  `base = (a[base + half] < key) ? base + half : base;`. Compilers turn this ternary into a `cmov`.
- The loop's trip count, about log₂(n), depends only on `n`, not on the data, so the loop branch itself is
  predictable.
- Finish with one final comparison to resolve the last element.

## Solution

```cpp
#pragma once
#include <cstddef>
#include <cstdint>
#include <span>

inline std::size_t branchless_lower_bound(std::span<const std::int64_t> a, std::int64_t key) {
    std::size_t n = a.size();
    if (n == 0) return 0;
    const std::int64_t* base = a.data();
    while (n > 1) {
        std::size_t half = n / 2;
        base = (base[half] < key) ? base + half : base;   // cmov, not a branch
        n -= half;
    }
    return static_cast<std::size_t>(base - a.data()) + (*base < key);
}
```

Each iteration does a load, a compare and a conditional move, so the only branch is the loop counter. The
trade-off: the CPU can no longer *speculate* down one side and start the next load early, so when the array
doesn't fit in cache, the classic version can win. Adding `__builtin_prefetch` for both possible next probes
fixes that. Measure both on your data.

## Tests

```cpp
#include <algorithm>
#include <random>
#include <vector>
int main() {
    std::vector<std::int64_t> a{1, 3, 3, 5, 8, 13};
    CHECK_EQ(branchless_lower_bound(a, 0), 0u);
    CHECK_EQ(branchless_lower_bound(a, 3), 1u);
    CHECK_EQ(branchless_lower_bound(a, 4), 3u);
    CHECK_EQ(branchless_lower_bound(a, 13), 5u);
    CHECK_EQ(branchless_lower_bound(a, 14), 6u);
    CHECK_EQ(branchless_lower_bound(std::vector<std::int64_t>{}, 5), 0u);
    CHECK_EQ(branchless_lower_bound(std::vector<std::int64_t>{7}, 7), 0u);
    CHECK_EQ(branchless_lower_bound(std::vector<std::int64_t>{7}, 8), 1u);
    std::mt19937_64 rng(5);
    bool ok = true;
    for (int trial = 0; trial < 200; ++trial) {
        std::vector<std::int64_t> v(rng() % 300);
        for (auto& x : v) x = std::int64_t(rng() % 1000);
        std::sort(v.begin(), v.end());
        for (int q = 0; q < 50; ++q) {
            std::int64_t key = std::int64_t(rng() % 1100) - 50;
            ok &= branchless_lower_bound(v, key) == std::size_t(std::lower_bound(v.begin(), v.end(), key) - v.begin());
        }
    }
    CHECK(ok);
    return judge::report();
}
```
