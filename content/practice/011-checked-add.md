+++
title = "Overflow-Safe Position Arithmetic"
difficulty = "easy"
tags = ["ub", "numerics"]
+++

A risk check adds a proposed order's quantity to the current position. Signed overflow is **undefined
behaviour**, so `pos + qty > limit` can't be trusted when values are extreme. The optimizer may even delete an
overflow check that is written after the addition.

Implement:

```cpp
std::optional<std::int64_t> checked_add(std::int64_t a, std::int64_t b);   // nullopt on overflow
bool within_limit(std::int64_t position, std::int64_t qty, std::int64_t limit); // |position + qty| <= limit
```

`within_limit` must return `false` (never invoke UB) if `position + qty` overflows, or if its absolute value
can't be represented. `limit >= 0`.

## Starter Code

```cpp
#pragma once
#include <cstdint>
#include <optional>

inline std::optional<std::int64_t> checked_add(std::int64_t a, std::int64_t b) {
    return a + b;   // BUG: undefined behaviour on overflow
}

inline bool within_limit(std::int64_t position, std::int64_t qty, std::int64_t limit) {
    return false;  // TODO
}
```

## Hints

- Check *before* adding: if `b > 0`, overflow happens exactly when `a > INT64_MAX - b`.
- GCC and Clang provide `__builtin_add_overflow(a, b, &result)`, which returns true on overflow and compiles to an
  `add` plus a `jo` (jump-if-overflow) instruction.
- `std::abs(INT64_MIN)` is itself UB.

## Solution

```cpp
#pragma once
#include <cstdint>
#include <limits>
#include <optional>

inline std::optional<std::int64_t> checked_add(std::int64_t a, std::int64_t b) {
    using L = std::numeric_limits<std::int64_t>;
    if (b > 0 && a > L::max() - b) return std::nullopt;
    if (b < 0 && a < L::min() - b) return std::nullopt;
    return a + b;
}

inline bool within_limit(std::int64_t position, std::int64_t qty, std::int64_t limit) {
    auto sum = checked_add(position, qty);
    if (!sum) return false;
    return *sum >= -limit && *sum <= limit;      // avoids std::abs(INT64_MIN)
}
```

Both comparisons, `a > max - b` and `a < min - b`, are computed without overflowing. In production you'd use
`__builtin_add_overflow` (or C++26's `std::add_sat` for saturating arithmetic). Comparing against `±limit`
instead of calling `std::abs` sidesteps the one value whose absolute value doesn't fit.

## Tests

```cpp
int main() {
    using L = std::numeric_limits<std::int64_t>;
    CHECK(checked_add(1, 2) == std::optional<std::int64_t>{3});
    CHECK(!checked_add(L::max(), 1));
    CHECK(!checked_add(L::min(), -1));
    CHECK(checked_add(L::max(), L::min()) == std::optional<std::int64_t>{-1});
    CHECK(checked_add(L::max() - 5, 5) == std::optional<std::int64_t>{L::max()});
    CHECK(within_limit(100, 50, 150));
    CHECK(!within_limit(100, 51, 150));
    CHECK(within_limit(-100, -50, 150));
    CHECK(!within_limit(L::max(), 1, L::max()));
    CHECK(!within_limit(L::min(), 0, L::max()));
    CHECK(within_limit(L::min() + 1, 0, L::max()));
    return judge::report();
}
```
