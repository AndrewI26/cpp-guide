+++
title = "Convert Prices to Ticks"
difficulty = "easy"
tags = ["numerics", "finance"]
+++

Trading systems store prices as integer multiples of the instrument's **tick size**. Implement:

```cpp
std::int64_t to_ticks(double price, double tick_size);   // nearest tick, ties away from zero
double from_ticks(std::int64_t ticks, double tick_size);
```

`to_ticks` must round to the **nearest** tick, so binary floating-point error never moves a price by a tick:
`to_ticks(0.29, 0.01)` must be `29`, even though `0.29 / 0.01 == 28.999999999999996`.

**Constraints:** `tick_size > 0`; `|price / tick_size| < 2⁵²`. Negative prices are valid (spreads, some futures).

## Starter Code

```cpp
#pragma once
#include <cstdint>

inline std::int64_t to_ticks(double price, double tick_size) {
    return static_cast<std::int64_t>(price / tick_size);   // BUG: truncates
}

inline double from_ticks(std::int64_t ticks, double tick_size) {
    return 0.0;  // TODO
}
```

## Hints

- `static_cast` truncates toward zero. You want rounding to the nearest integer.
- `<cmath>` has `std::llround`, which rounds half away from zero.

## Solution

```cpp
#pragma once
#include <cmath>
#include <cstdint>

inline std::int64_t to_ticks(double price, double tick_size) {
    return static_cast<std::int64_t>(std::llround(price / tick_size));
}

inline double from_ticks(std::int64_t ticks, double tick_size) {
    return static_cast<double>(ticks) * tick_size;
}
```

`price / tick_size` lands *very close* to an integer, but possibly just below it. Rounding to the nearest
integer absorbs that error. Convert once, at the system boundary, and do all internal arithmetic in integers.
See Chapter 1.3, §2.

## Tests

```cpp
int main() {
    CHECK_EQ(to_ticks(0.29, 0.01), 29);
    CHECK_EQ(to_ticks(1.15, 0.01), 115);
    CHECK_EQ(to_ticks(4.35, 0.01), 435);
    CHECK_EQ(to_ticks(101.25, 0.25), 405);
    CHECK_EQ(to_ticks(-0.29, 0.01), -29);
    CHECK_EQ(to_ticks(0.0, 0.05), 0);
    CHECK_EQ(to_ticks(1234.5678, 0.0001), 12345678);
    CHECK_NEAR(from_ticks(405, 0.25), 101.25, 1e-12);
    CHECK_NEAR(from_ticks(-29, 0.01), -0.29, 1e-12);
    bool round_trip = true;
    for (int i = -100000; i <= 100000; ++i) round_trip &= to_ticks(from_ticks(i, 0.01), 0.01) == i;
    CHECK(round_trip);
    return judge::report();
}
```
