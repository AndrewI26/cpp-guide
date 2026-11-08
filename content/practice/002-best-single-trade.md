+++
title = "Best Single Round Trip"
difficulty = "easy"
tags = ["algorithms", "finance"]
+++

Given a series of prices `p[0..n-1]`, find the maximum profit from **one** buy followed later by **one** sell:
`max(p[j] - p[i])` over `i < j`. If no profitable trade exists, return 0.

Implement `std::int64_t best_round_trip(std::span<const std::int64_t> prices)` in **O(n)** time and O(1)
extra space. Prices are integer ticks.

**Example:** `[7, 1, 5, 3, 6, 4]` → buy at 1, sell at 6 → `5`.

## Starter Code

```cpp
#pragma once
#include <cstdint>
#include <span>

inline std::int64_t best_round_trip(std::span<const std::int64_t> prices) {
    // TODO
    return 0;
}
```

## Hints

- The brute force is O(n²). What single value from the past do you need to know when you're at day `j`?
- Track the minimum price seen so far.

## Solution

```cpp
#pragma once
#include <algorithm>
#include <cstdint>
#include <limits>
#include <span>

inline std::int64_t best_round_trip(std::span<const std::int64_t> prices) {
    std::int64_t lowest = std::numeric_limits<std::int64_t>::max();
    std::int64_t best = 0;
    for (std::int64_t p : prices) {
        lowest = std::min(lowest, p);
        best = std::max(best, p - lowest);
    }
    return best;
}
```

At each price the best sale ending here is `p - min(previous prices)`. Track the running minimum and the best
difference. Both `std::min` and `std::max` usually compile to branchless `cmov` instructions.

## Tests

```cpp
#include <vector>
int main() {
    CHECK_EQ(best_round_trip(std::vector<std::int64_t>{7, 1, 5, 3, 6, 4}), 5);
    CHECK_EQ(best_round_trip(std::vector<std::int64_t>{7, 6, 4, 3, 1}), 0);
    CHECK_EQ(best_round_trip(std::vector<std::int64_t>{}), 0);
    CHECK_EQ(best_round_trip(std::vector<std::int64_t>{5}), 0);
    CHECK_EQ(best_round_trip(std::vector<std::int64_t>{2, 4, 1, 10}), 9);
    CHECK_EQ(best_round_trip(std::vector<std::int64_t>{-5, -1, -10, -2}), 8);
    std::vector<std::int64_t> big(1'000'000);
    for (int i = 0; i < 1'000'000; ++i) big[i] = (i * 7919LL) % 100003;
    CHECK_EQ(best_round_trip(big), 100002);
    return judge::report();
}
```
