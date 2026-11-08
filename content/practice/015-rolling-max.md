+++
title = "Highest High Over a Window"
difficulty = "medium"
tags = ["algorithms", "data-structures", "finance"]
+++

Breakout strategies track the highest high of the last `k` bars. Given prices and `k`, return a vector where
element `i` is the maximum of `prices[max(0, i-k+1) .. i]`.

```cpp
std::vector<std::int64_t> rolling_max(std::span<const std::int64_t> prices, std::size_t k);  // k >= 1
```

The O(n·k) approach is too slow for `n = 10⁶, k = 10⁴`. Achieve **O(n)** total.

**Example:** `[1, 3, 2, 5, 4, 1]`, k = 3 → `[1, 3, 3, 5, 5, 5]`.

## Starter Code

```cpp
#pragma once
#include <cstddef>
#include <cstdint>
#include <span>
#include <vector>

inline std::vector<std::int64_t> rolling_max(std::span<const std::int64_t> prices, std::size_t k) {
    return {};  // TODO
}
```

## Hints

- Keep a deque of *indices* whose prices are decreasing from front to back.
- When a new price arrives, pop from the back every index whose price is ≤ the new one: those can never be the
  maximum again.
- Pop from the front when that index leaves the window. The front is always the current maximum.

## Solution

```cpp
#pragma once
#include <cstddef>
#include <cstdint>
#include <deque>
#include <span>
#include <vector>

inline std::vector<std::int64_t> rolling_max(std::span<const std::int64_t> prices, std::size_t k) {
    std::vector<std::int64_t> out;
    out.reserve(prices.size());
    std::deque<std::size_t> dq;                 // indices, prices strictly decreasing front to back
    for (std::size_t i = 0; i < prices.size(); ++i) {
        while (!dq.empty() && prices[dq.back()] <= prices[i]) dq.pop_back();
        dq.push_back(i);
        if (dq.front() + k <= i) dq.pop_front();   // left the window
        out.push_back(prices[dq.front()]);
    }
    return out;
}
```

This is the **monotonic deque**. Each index is pushed once and popped at most once, so the total work is O(n)
however large `k` is. For the hot path you can replace `std::deque` with a fixed ring buffer of capacity `k`
(see "Fixed-Capacity Ring Buffer").

## Tests

```cpp
int main() {
    using V = std::vector<std::int64_t>;
    CHECK(rolling_max(V{1, 3, 2, 5, 4, 1}, 3) == (V{1, 3, 3, 5, 5, 5}));
    CHECK(rolling_max(V{5, 4, 3, 2, 1}, 2) == (V{5, 5, 4, 3, 2}));
    CHECK(rolling_max(V{1, 2, 3}, 1) == (V{1, 2, 3}));
    CHECK(rolling_max(V{}, 4).empty());
    CHECK(rolling_max(V{2, 2, 2}, 10) == (V{2, 2, 2}));
    V big(1'000'000);
    for (std::size_t i = 0; i < big.size(); ++i) big[i] = std::int64_t((i * 2654435761u) % 1000003);
    auto r = rolling_max(big, 10000);
    bool ok = r.size() == big.size();
    for (std::size_t i : {0ul, 9999ul, 10000ul, 500000ul, 999999ul}) {
        std::int64_t m = big[i];
        for (std::size_t j = (i >= 9999 ? i - 9999 : 0); j <= i; ++j) m = std::max(m, big[j]);
        ok &= r[i] == m;
    }
    CHECK(ok);
    return judge::report();
}
```
