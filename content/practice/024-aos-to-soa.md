+++
title = "Struct of Arrays for Fast Scans"
difficulty = "medium"
tags = ["data-layout", "cache", "memory"]
+++

Positions are stored as an array of structs:

```cpp
struct Position {
    std::int64_t instrument_id;
    char         account[24];
    double       qty;
    double       price;
    double       multiplier;
    std::int64_t last_update_ns;
};   // 64 bytes
```

A risk check runs every few microseconds and only needs `qty * price * multiplier`. That uses 24 of the 64 bytes
fetched per position.

Implement a struct-of-arrays container and the two computations:

```cpp
struct PositionsSoA {
    std::vector<double> qty, price, multiplier;
};
PositionsSoA to_soa(std::span<const Position> aos);
double gross_notional(const PositionsSoA& p);   // Σ |qty · price · multiplier|
double net_notional(const PositionsSoA& p);     // Σ  qty · price · multiplier
```

## Starter Code

```cpp
#pragma once
#include <cmath>
#include <cstdint>
#include <span>
#include <vector>

struct Position {
    std::int64_t instrument_id;
    char account[24];
    double qty;
    double price;
    double multiplier;
    std::int64_t last_update_ns;
};

struct PositionsSoA {
    std::vector<double> qty, price, multiplier;
};

inline PositionsSoA to_soa(std::span<const Position> aos) { return {}; }
inline double gross_notional(const PositionsSoA& p) { return 0; }
inline double net_notional(const PositionsSoA& p) { return 0; }
```

## Hints

- `reserve` each vector once.
- Write the scan as a plain indexed loop over three arrays. The compiler can vectorize it (at `-O3`, or with
  `-ffast-math` for the reduction), and each cache line now holds 8 useful doubles instead of fewer than 3.

## Solution

```cpp
#pragma once
#include <cmath>
#include <cstdint>
#include <span>
#include <vector>

struct Position {
    std::int64_t instrument_id;
    char account[24];
    double qty;
    double price;
    double multiplier;
    std::int64_t last_update_ns;
};

struct PositionsSoA {
    std::vector<double> qty, price, multiplier;
};

inline PositionsSoA to_soa(std::span<const Position> aos) {
    PositionsSoA s;
    s.qty.reserve(aos.size());
    s.price.reserve(aos.size());
    s.multiplier.reserve(aos.size());
    for (const Position& p : aos) {
        s.qty.push_back(p.qty);
        s.price.push_back(p.price);
        s.multiplier.push_back(p.multiplier);
    }
    return s;
}

inline double gross_notional(const PositionsSoA& p) {
    double total = 0;
    const std::size_t n = p.qty.size();
    for (std::size_t i = 0; i < n; ++i) total += std::fabs(p.qty[i] * p.price[i] * p.multiplier[i]);
    return total;
}

inline double net_notional(const PositionsSoA& p) {
    double total = 0;
    const std::size_t n = p.qty.size();
    for (std::size_t i = 0; i < n; ++i) total += p.qty[i] * p.price[i] * p.multiplier[i];
    return total;
}
```

The scan reads 24 bytes per position instead of 64, and every byte fetched is used. That matters most when the
data doesn't fit in cache. Chapter 5.3 measured a 9× difference for a single field. In a real system you'd keep
the data in SoA form permanently rather than converting it for each check.

## Tests

```cpp
int main() {
    std::vector<Position> v(4);
    double q[] = {10, -5, 2, 0}, px[] = {100, 50, 25, 1}, m[] = {1, 2, 10, 1};
    for (int i = 0; i < 4; ++i) { v[i].qty = q[i]; v[i].price = px[i]; v[i].multiplier = m[i]; }
    auto s = to_soa(v);
    CHECK(s.qty.size() == 4 && s.price[2] == 25 && s.multiplier[1] == 2);
    CHECK_NEAR(gross_notional(s), 1000 + 500 + 500, 1e-9);
    CHECK_NEAR(net_notional(s), 1000 - 500 + 500, 1e-9);
    CHECK_EQ(sizeof(Position), 64u);
    std::vector<Position> big(100000);
    for (std::size_t i = 0; i < big.size(); ++i) { big[i].qty = (i % 2 ? 1 : -1); big[i].price = 2; big[i].multiplier = 3; }
    auto b = to_soa(big);
    CHECK_NEAR(gross_notional(b), 600000, 1e-6);
    CHECK_NEAR(net_notional(b), 0, 1e-6);
    return judge::report();
}
```
