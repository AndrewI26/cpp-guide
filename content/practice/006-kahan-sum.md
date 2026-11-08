+++
title = "Compensated Summation"
difficulty = "easy"
tags = ["numerics"]
+++

Summing many floating-point numbers loses precision: each addition rounds, and when you add a small number to a
large running total, most of the small number's digits fall off the end. **Kahan summation** keeps a running
compensation term holding the low-order bits that were lost.

Implement `double kahan_sum(std::span<const double> xs)` using Kahan (or Neumaier) compensated summation.

The test sums `1.0` followed by ten million copies of `1e-16`. A naive loop returns exactly `1.0`, because
`1.0 + 1e-16 == 1.0`. The exact answer is `1.000000001`.

## Starter Code

```cpp
#pragma once
#include <span>

inline double kahan_sum(std::span<const double> xs) {
    double s = 0.0;
    for (double x : xs) s += x;   // naive: loses the small terms
    return s;
}
```

## Hints

- Keep `sum` and `c` (the compensation). For each `x`: `y = x - c; t = sum + y; c = (t - sum) - y; sum = t;`
- Algebraically `c` is always 0. In floating point it captures exactly what the addition dropped.
- Don't compile this with `-ffast-math`. It's allowed to "simplify" `c` to zero.

## Solution

```cpp
#pragma once
#include <cmath>
#include <span>

// Neumaier's variant: also correct when the next term is larger than the running sum.
inline double kahan_sum(std::span<const double> xs) {
    double sum = 0.0, c = 0.0;
    for (double x : xs) {
        double t = sum + x;
        if (std::fabs(sum) >= std::fabs(x)) c += (sum - t) + x;   // low bits of x were lost
        else                                c += (x - t) + sum;   // low bits of sum were lost
        sum = t;
    }
    return sum + c;
}
```

`(sum - t) + x` recovers, exactly, the rounding error of `sum + x`. Accumulating those errors in `c` and adding
them back at the end gives a result as accurate as summing in roughly twice the precision. Classic Kahan fails
when a term is larger than the running sum (try `[1, 1e100, 1, -1e100]`); Neumaier's branch fixes that.

## Tests

```cpp
#include <vector>
int main() {
    std::vector<double> v(10'000'001, 1e-16);
    v[0] = 1.0;
    CHECK_NEAR(kahan_sum(v), 1.000000001, 1e-15);
    CHECK_EQ(kahan_sum(std::vector<double>{}), 0.0);
    CHECK_NEAR(kahan_sum(std::vector<double>{0.1, 0.2, 0.3}), 0.6, 1e-16);
    CHECK_EQ(kahan_sum(std::vector<double>{1.0, 1e100, 1.0, -1e100}), 2.0);
    std::vector<double> alt;
    for (int i = 0; i < 1000000; ++i) alt.push_back(i % 2 ? -0.1 : 0.1);
    CHECK_NEAR(kahan_sum(alt), 0.0, 1e-15);
    return judge::report();
}
```
