+++
title = "Log Returns and Volatility"
difficulty = "easy"
tags = ["stl", "numerics", "finance"]
+++

From a series of closing prices, compute:

```cpp
std::vector<double> log_returns(std::span<const double> prices);   // r_i = ln(p_i / p_{i-1}), size n-1
double annualized_vol(std::span<const double> prices, double periods_per_year);
```

`annualized_vol` is the **sample** standard deviation (divide by n−1) of the log returns, times
`sqrt(periods_per_year)` (252 for daily data). Return an empty vector, or 0.0, when there are fewer than 2
prices (`annualized_vol` needs at least 2 returns).

## Starter Code

```cpp
#pragma once
#include <cmath>
#include <span>
#include <vector>

inline std::vector<double> log_returns(std::span<const double> prices) {
    return {};  // TODO
}

inline double annualized_vol(std::span<const double> prices, double periods_per_year) {
    return 0.0;  // TODO
}
```

## Hints

- `reserve` the output vector so it allocates exactly once.
- Sample variance: `Σ (r - mean)² / (n - 1)`. Two passes over the returns are fine here.

## Solution

```cpp
#pragma once
#include <cmath>
#include <numeric>
#include <span>
#include <vector>

inline std::vector<double> log_returns(std::span<const double> prices) {
    std::vector<double> r;
    if (prices.size() < 2) return r;
    r.reserve(prices.size() - 1);
    for (std::size_t i = 1; i < prices.size(); ++i) r.push_back(std::log(prices[i] / prices[i - 1]));
    return r;
}

inline double annualized_vol(std::span<const double> prices, double periods_per_year) {
    std::vector<double> r = log_returns(prices);
    if (r.size() < 2) return 0.0;
    double mean = std::accumulate(r.begin(), r.end(), 0.0) / double(r.size());
    double ss = 0.0;
    for (double x : r) ss += (x - mean) * (x - mean);
    return std::sqrt(ss / double(r.size() - 1)) * std::sqrt(periods_per_year);
}
```

Log returns add over time (the sum of daily log returns is the log return over the period), which is why
volatility is quoted from them. The two-pass variance is numerically safer than the `E[x²] − E[x]²` shortcut.

## Tests

```cpp
int main() {
    std::vector<double> p{100, 110, 99, 99};
    auto r = log_returns(p);
    CHECK_EQ(r.size(), 3u);
    CHECK_NEAR(r[0], std::log(1.1), 1e-15);
    CHECK_NEAR(r[1], std::log(0.9), 1e-15);
    CHECK_NEAR(r[2], 0.0, 1e-15);
    CHECK(log_returns(std::vector<double>{100}).empty());
    CHECK_EQ(annualized_vol(std::vector<double>{100, 101}, 252), 0.0);
    std::vector<double> q{100, 102, 100, 102, 100};
    double a = std::log(1.02), b = -a, mean = 0;
    double sd = std::sqrt((2 * (a - mean) * (a - mean) + 2 * (b - mean) * (b - mean)) / 3);
    CHECK_NEAR(annualized_vol(q, 252), sd * std::sqrt(252.0), 1e-12);
    return judge::report();
}
```
