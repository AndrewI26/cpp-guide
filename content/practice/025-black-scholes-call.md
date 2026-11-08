+++
title = "Black–Scholes Call and Put"
difficulty = "medium"
tags = ["numerics", "finance"]
+++

Implement the Black–Scholes price of a European call and put on a non-dividend-paying stock:

```text
d1 = (ln(S/K) + (r + σ²/2)·T) / (σ·√T),   d2 = d1 − σ·√T
call = S·N(d1) − K·e^(−rT)·N(d2)
put  = K·e^(−rT)·N(−d2) − S·N(−d1)
```

```cpp
double norm_cdf(double x);
double bs_call(double S, double K, double r, double sigma, double T);
double bs_put(double S, double K, double r, double sigma, double T);
```

`N` is the standard normal CDF. It must be accurate in the tails (the test checks N(−9) to relative precision).
Assume `S, K, sigma, T > 0`. The **Option Pricer** project extends this to Greeks, implied volatility and Monte
Carlo.

## Starter Code

```cpp
#pragma once
#include <cmath>

inline double norm_cdf(double x) { return 0.0; }
inline double bs_call(double S, double K, double r, double sigma, double T) { return 0.0; }
inline double bs_put(double S, double K, double r, double sigma, double T) { return 0.0; }
```

## Hints

- N(x) = ½·erfc(−x/√2). Use `std::erfc`, not `1 + std::erf(...)`, to avoid cancellation in the left tail.
- Check yourself with put–call parity: call − put = S − K·e^(−rT).

## Solution

```cpp
#pragma once
#include <cmath>

inline double norm_cdf(double x) { return 0.5 * std::erfc(-x / std::sqrt(2.0)); }

inline double bs_call(double S, double K, double r, double sigma, double T) {
    double sqT = std::sqrt(T);
    double d1 = (std::log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqT);
    double d2 = d1 - sigma * sqT;
    return S * norm_cdf(d1) - K * std::exp(-r * T) * norm_cdf(d2);
}

inline double bs_put(double S, double K, double r, double sigma, double T) {
    double sqT = std::sqrt(T);
    double d1 = (std::log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqT);
    double d2 = d1 - sigma * sqT;
    return K * std::exp(-r * T) * norm_cdf(-d2) - S * norm_cdf(-d1);
}
```

`erfc` computes the tail directly, so N(−9) ≈ 1.13 × 10⁻¹⁹ keeps full relative precision. With
`0.5 * (1 + erf(x/√2))` it would round to exactly 0. Computing the put directly (rather than from the call via
parity) avoids cancellation for deep in-the-money calls.

## Tests

```cpp
int main() {
    CHECK_NEAR(norm_cdf(0), 0.5, 1e-15);
    CHECK_NEAR(norm_cdf(1.0), 0.8413447460685429, 1e-14);
    CHECK_NEAR(norm_cdf(-9.0) / 1.1285884059538408e-19, 1.0, 1e-9);
    CHECK_NEAR(bs_call(100, 100, 0.05, 0.2, 1.0), 10.450583572185565, 1e-9);
    CHECK_NEAR(bs_put(100, 100, 0.05, 0.2, 1.0), 5.573526022256971, 1e-9);
    bool parity = true;
    for (double K : {50.0, 90.0, 100.0, 130.0, 200.0})
        for (double T : {0.01, 0.5, 5.0})
            parity &= std::fabs(bs_call(100, K, 0.03, 0.35, T) - bs_put(100, K, 0.03, 0.35, T) - (100 - K * std::exp(-0.03 * T))) < 1e-9;
    CHECK(parity);
    CHECK(bs_call(100, 1000, 0.0, 0.2, 0.1) >= 0.0);
    CHECK_NEAR(bs_call(100, 1, 0.0, 0.2, 1.0), 99.0, 1e-9);
    return judge::report();
}
```
