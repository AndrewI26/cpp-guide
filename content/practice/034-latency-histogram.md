+++
title = "Latency Histogram with Percentiles"
difficulty = "hard"
tags = ["measuring", "data-structures", "bit-manipulation"]
+++

Averages hide the latency outliers that matter. Production systems record **every** latency sample into a
histogram and report percentiles. Storing every sample and sorting would be far too expensive, so implement a
compact **log-linear** histogram, in the spirit of HdrHistogram:

- Values from 0 to 2⁴⁰ ns are recordable (larger values are clamped to the top bucket).
- Values below 64 each get an exact bucket.
- Above that, each power-of-two range `[2^k, 2^(k+1))` is split into **32 equal sub-buckets**, so the relative
  error of a reported value is under about 3%.

```cpp
class LatencyHistogram {
public:
    void record(std::uint64_t ns);              // O(1), no allocation
    std::uint64_t count() const;
    std::uint64_t percentile(double p) const;   // p in [0, 100]: the upper bound of the bucket holding the
                                                // ceil(p/100 * count)-th smallest sample (min 1st); 0 if empty
    std::uint64_t max() const;                  // exact maximum recorded
};
```

The test records known distributions and checks that percentiles come back within 1/32 relative error.

## Starter Code

```cpp
#pragma once
#include <array>
#include <bit>
#include <cmath>
#include <cstdint>

class LatencyHistogram {
public:
    void record(std::uint64_t ns) {}
    std::uint64_t count() const { return 0; }
    std::uint64_t percentile(double p) const { return 0; }
    std::uint64_t max() const { return 0; }
};
```

## Hints

- Bucket index for `v >= 64`: let `k = bit_width(v) - 1` (so `2^k <= v < 2^(k+1)`). The sub-bucket is the next 5
  bits below the top bit: `(v >> (k - 5)) & 31`. The index is `64 + (k - 6) * 32 + sub`.
- Upper bound of a bucket (the largest value that maps to it): `((32 + sub + 1) << (k - 5)) - 1`.
- `percentile`: compute the target rank, then walk the buckets accumulating counts until you reach it.

## Solution

```cpp
#pragma once
#include <algorithm>
#include <array>
#include <bit>
#include <cmath>
#include <cstdint>

class LatencyHistogram {
    static constexpr int kMaxBits = 40;
    static constexpr std::size_t kBuckets = 64 + (kMaxBits - 6) * 32;
public:
    void record(std::uint64_t ns) {
        ns = std::min<std::uint64_t>(ns, (std::uint64_t{1} << kMaxBits) - 1);
        ++counts_[index(ns)];
        ++total_;
        max_ = std::max(max_, ns);
    }
    std::uint64_t count() const { return total_; }
    std::uint64_t max() const { return max_; }

    std::uint64_t percentile(double p) const {
        if (total_ == 0) return 0;
        auto rank = static_cast<std::uint64_t>(std::ceil(p / 100.0 * double(total_)));
        rank = std::clamp<std::uint64_t>(rank, 1, total_);
        std::uint64_t seen = 0;
        for (std::size_t i = 0; i < kBuckets; ++i) {
            seen += counts_[i];
            if (seen >= rank) return std::min(upper(i), max_);
        }
        return max_;
    }

private:
    static std::size_t index(std::uint64_t v) {
        if (v < 64) return static_cast<std::size_t>(v);
        int k = std::bit_width(v) - 1;                       // 2^k <= v < 2^(k+1), k >= 6
        std::size_t sub = static_cast<std::size_t>((v >> (k - 5)) & 31);
        return 64 + static_cast<std::size_t>(k - 6) * 32 + sub;
    }
    static std::uint64_t upper(std::size_t i) {
        if (i < 64) return i;
        std::size_t k = (i - 64) / 32 + 6, sub = (i - 64) % 32;
        return ((32 + sub + 1) << (k - 5)) - 1;
    }

    std::array<std::uint64_t, kBuckets> counts_{};
    std::uint64_t total_ = 0, max_ = 0;
};
```

Recording a sample is a `bit_width` (one `lzcnt`), a shift, a mask and an increment: a couple of nanoseconds,
cheap enough to time every message. The whole histogram is 1,152 buckets × 8 bytes ≈ 9 KB, independent of the
number of samples. Two histograms can be merged by adding their bucket counts, so each thread can keep its own.

## Tests

```cpp
#include <random>
static bool close(std::uint64_t got, std::uint64_t want) {
    double rel = want ? std::fabs(double(got) - double(want)) / double(want) : double(got);
    return rel <= 1.0 / 32.0 + 1e-12;
}
int main() {
    LatencyHistogram h;
    CHECK_EQ(h.percentile(50), 0u);
    for (std::uint64_t v = 1; v <= 100000; ++v) h.record(v);
    CHECK_EQ(h.count(), 100000u);
    CHECK(close(h.percentile(50), 50000));
    CHECK(close(h.percentile(99), 99000));
    CHECK(close(h.percentile(99.9), 99900));
    CHECK_EQ(h.percentile(100), 100000u);
    CHECK_EQ(h.max(), 100000u);
    CHECK_EQ(h.percentile(0), 1u);
    LatencyHistogram small;
    for (std::uint64_t v : {5, 5, 5, 7, 63}) small.record(v);
    CHECK_EQ(small.percentile(50), 5u);
    CHECK_EQ(small.percentile(80), 7u);
    CHECK_EQ(small.percentile(100), 63u);
    LatencyHistogram tail;
    std::mt19937_64 rng(2);
    for (int i = 0; i < 1'000'000; ++i) tail.record(rng() % 1000 == 0 ? 50'000 + rng() % 1000 : 200 + rng() % 50);
    CHECK(tail.percentile(50) >= 200 && tail.percentile(50) <= 260);
    CHECK(close(tail.percentile(99.95), 50'500) || tail.percentile(99.95) >= 49'000);
    LatencyHistogram huge;
    huge.record(1ull << 50);
    CHECK(huge.percentile(100) == (1ull << 40) - 1);
    return judge::report();
}
```
