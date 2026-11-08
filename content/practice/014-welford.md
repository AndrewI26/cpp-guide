+++
title = "Online Mean and Variance"
difficulty = "medium"
tags = ["numerics", "finance"]
+++

You need the running mean and variance of a stream of trade sizes or returns, in O(1) memory. The textbook
shortcut `Var = E[x²] − E[x]²` suffers from catastrophic cancellation when the mean is large relative to the
spread. With prices near 10⁶ and variation in the cents, it can even return a **negative** variance.

Implement Welford's algorithm:

```cpp
class RunningStats {
public:
    void add(double x);
    std::size_t count() const;
    double mean() const;               // 0 if empty
    double variance() const;           // sample variance (n-1); 0 if count < 2
    double stddev() const;
    void merge(const RunningStats& other);   // combine two partial results (e.g. from two threads)
};
```

## Starter Code

```cpp
#pragma once
#include <cmath>
#include <cstddef>

class RunningStats {
public:
    void add(double x) {}
    std::size_t count() const { return 0; }
    double mean() const { return 0; }
    double variance() const { return 0; }
    double stddev() const { return 0; }
    void merge(const RunningStats& other) {}
};
```

## Hints

- Keep `n`, `mean` and `M2`, the sum of squared deviations from the current mean.
- Update: `delta = x - mean; mean += delta / n; M2 += delta * (x - mean);` (note the *new* mean in the last factor).
- Merging (Chan et al.): `delta = b.mean - a.mean; M2 = a.M2 + b.M2 + delta² · a.n · b.n / n`.

## Solution

```cpp
#pragma once
#include <cmath>
#include <cstddef>

class RunningStats {
public:
    void add(double x) {
        ++n_;
        double delta = x - mean_;
        mean_ += delta / static_cast<double>(n_);
        m2_ += delta * (x - mean_);
    }
    std::size_t count() const { return n_; }
    double mean() const { return mean_; }
    double variance() const { return n_ < 2 ? 0.0 : m2_ / static_cast<double>(n_ - 1); }
    double stddev() const { return std::sqrt(variance()); }
    void merge(const RunningStats& o) {
        if (o.n_ == 0) return;
        if (n_ == 0) { *this = o; return; }
        double na = double(n_), nb = double(o.n_), n = na + nb;
        double delta = o.mean_ - mean_;
        mean_ += delta * nb / n;
        m2_ += o.m2_ + delta * delta * na * nb / n;
        n_ += o.n_;
    }
private:
    std::size_t n_ = 0;
    double mean_ = 0.0, m2_ = 0.0;
};
```

Welford's algorithm only ever accumulates squared deviations from the current mean, which are small, instead of
squares of the raw values, which are huge. That avoids subtracting two nearly equal large numbers. `merge` lets
you compute statistics in parallel and combine them exactly.

## Tests

```cpp
#include <vector>
int main() {
    RunningStats s;
    CHECK_EQ(s.variance(), 0.0);
    for (double x : {2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0}) s.add(x);
    CHECK_EQ(s.count(), 8u);
    CHECK_NEAR(s.mean(), 5.0, 1e-12);
    CHECK_NEAR(s.variance(), 32.0 / 7.0, 1e-12);
    RunningStats big;
    for (int i = 0; i < 1'000'000; ++i) big.add(1e9 + (i % 2 ? 0.01 : -0.01));
    CHECK_NEAR(big.mean(), 1e9, 1e-6);
    CHECK_NEAR(big.variance(), 1e-4 * 1e6 / (1e6 - 1), 1e-9);
    RunningStats a, b, all;
    for (int i = 0; i < 1000; ++i) { double x = (i * 37 % 101) * 0.5; (i < 300 ? a : b).add(x); all.add(x); }
    a.merge(b);
    CHECK_EQ(a.count(), 1000u);
    CHECK_NEAR(a.mean(), all.mean(), 1e-12);
    CHECK_NEAR(a.variance(), all.variance(), 1e-9);
    RunningStats empty;
    empty.merge(all);
    CHECK_NEAR(empty.mean(), all.mean(), 1e-12);
    return judge::report();
}
```
