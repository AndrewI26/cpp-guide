+++
title = "VWAP of a Trade Tape"
difficulty = "easy"
tags = ["stl", "finance"]
+++

The **volume-weighted average price** (VWAP) of a set of trades is the total notional traded divided by the
total quantity:

```text
VWAP = Σ price_i · qty_i  /  Σ qty_i
```

It's the most common execution benchmark: a broker's fill quality is often judged against the day's VWAP.

Implement `double vwap(std::span<const Trade> trades)`. Return `0.0` for an empty tape, or if the total
quantity is zero.

**Example:** trades `(100.0, 10)`, `(101.0, 30)` → `(1000 + 3030) / 40 = 100.75`.

**Constraints:** up to 10⁷ trades; quantities are non-negative and fit in `int64_t`. Accumulate the quantity
in an integer.

## Starter Code

```cpp
#pragma once
#include <cstdint>
#include <span>

struct Trade {
    double price;
    std::int64_t qty;
};

inline double vwap(std::span<const Trade> trades) {
    // TODO
    return 0.0;
}
```

## Hints

- One pass, two accumulators: `notional` (double) and `volume` (int64).
- Watch out for dividing by zero.

## Solution

```cpp
#pragma once
#include <cstdint>
#include <span>

struct Trade {
    double price;
    std::int64_t qty;
};

inline double vwap(std::span<const Trade> trades) {
    double notional = 0.0;
    std::int64_t volume = 0;
    for (const Trade& t : trades) {
        notional += t.price * static_cast<double>(t.qty);
        volume += t.qty;
    }
    return volume > 0 ? notional / static_cast<double>(volume) : 0.0;
}
```

A single pass over contiguous memory, with no allocation. `std::span` accepts a `std::vector`, a `std::array` or
a C array without copying. Keeping `volume` as an integer avoids floating-point rounding in the count.

## Tests

```cpp
#include <vector>
int main() {
    CHECK_NEAR(vwap(std::vector<Trade>{{100.0, 10}, {101.0, 30}}), 100.75, 1e-12);
    CHECK_EQ(vwap(std::vector<Trade>{}), 0.0);
    CHECK_EQ(vwap(std::vector<Trade>{{99.0, 0}}), 0.0);
    CHECK_NEAR(vwap(std::vector<Trade>{{50.25, 1}}), 50.25, 1e-12);
    std::vector<Trade> many;
    for (int i = 0; i < 100000; ++i) many.push_back({100.0 + (i % 2), 5});
    CHECK_NEAR(vwap(many), 100.5, 1e-9);
    Trade arr[] = {{10.0, 1}, {20.0, 3}};
    CHECK_NEAR(vwap(arr), 17.5, 1e-12);
    return judge::report();
}
```
