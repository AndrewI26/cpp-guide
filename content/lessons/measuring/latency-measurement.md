+++
title = "Measuring Latency: Percentiles and Tails"
minutes = 45
practice_tags = ["measuring", "data-structures", "bit-manipulation"]
+++

## Why this chapter matters

Here are three measurements of the same operation, appending 4 million `int64_t`s to a `std::vector` one at a time,
timing each `push_back` individually on the test machine:

| Version | Mean | Median | p99 | p99.9 | p99.99 | Max |
|---|---|---|---|---|---|---|
| no `reserve` | 25.2 ns | 12.8 ns | 13.6 ns | 2,477 ns | 2,966 ns | 14.8 ms |
| `reserve` up front | 17.6 ns | 12.8 ns | 13.6 ns | 2,414 ns | 2,843 ns | 44 µs |
| `reserve` + pre-fault | 13.4 ns | 13.6 ns | 14.4 ns | 17.6 ns | 77 ns | 39 µs |

The medians are identical. The means differ by less than 2×. But one push in a thousand takes **2.4 µs** in the
first two versions, and the worst call in the first one takes **15 milliseconds**: the reallocation that copies 16 MB
into a new 32 MB block. `reserve` removes the reallocations, but not the 2.4 µs tail: those are the page faults from
Chapter 5.5, one each time the vector's writes reach a new 4 KB page (every 512 elements, so 0.2% of pushes). Only
pre-faulting removes them.

If a trading system's latency were described by its mean or median, none of this would be visible. Yet the
**tail** is what matters: the slow message is the one where you get picked off or miss the trade. This chapter is
about measuring latency so that the tail is visible and trustworthy.

By the end of this chapter you should be able to:

- explain why averages hide latency problems, and report percentiles instead;
- record latencies cheaply with histograms, including log-linear (HDR-style) histograms;
- use the timestamp counter (`rdtsc`) correctly for nanosecond timing;
- recognize and avoid coordinated omission in load tests.

---

## 1. Why averages lie

Latency distributions in real systems are **skewed**: most operations are fast, and a small fraction are much
slower because something extra happened (a cache miss, a page fault, a reallocation, an interrupt, a context
switch, a GC pause in some other process, a lock held by another thread). The mean blends these together into a
number that describes no actual operation. The median describes a typical operation and ignores the tail entirely.

**Percentiles** describe the distribution. The p99 is the value that 99% of operations are at or below; p99.9 and
p99.99 describe progressively rarer events. For a feed handler processing a million messages a second:

| Percentile | Exceeded by | At 1 M msgs/s, happens |
|---|---|---|
| p50 | half of messages | 500,000 times a second |
| p99 | 1 in 100 | 10,000 times a second |
| p99.9 | 1 in 1,000 | 1,000 times a second |
| p99.99 | 1 in 10,000 | 100 times a second |
| max | the single worst | once per measurement period |

"Rare" events at p99.99 happen a hundred times a second at that rate. That's why low-latency teams track p99.9,
p99.99 and max, and why the goal of much low-latency engineering is not making the median faster but **removing the
sources of the tail**: allocation, page faults, reallocation, locks, system calls and cache misses on the hot path.

> [!IMPORTANT]
> Always report latency as a distribution: at least p50, p99, p99.9 and max, plus the number of samples. A
> percentile is only meaningful with enough samples: p99.99 needs well over 10,000 measurements to be anything but
> noise.

### Exercises

:::exercise What does the mean hide?
A strategy reports a mean tick-to-trade latency of 4 µs. Describe two very different distributions with that same
mean, and which one you'd rather trade with.
:::solution
(A) Every event takes between 3.8 and 4.2 µs. (B) 99% of events take 2 µs, and 1% take about 200 µs (perhaps a log
flush or a page fault): mean = 0.99 × 2 + 0.01 × 200 ≈ 4 µs. B's median is better, but 1 in 100 orders arrives
100× late, which in a fast market is when you get filled at stale prices. A is far preferable; B has a tail problem
that the mean disguises completely.
:::

---

## 2. Timing individual events with the TSC

To measure the distribution you need a timestamp for each event, and the clock must be cheap compared with what
you're measuring. Measured on the test machine:

| Clock | Cost per read |
|---|---|
| `std::chrono::steady_clock::now()` (vDSO `clock_gettime`) | 22.2 ns |
| `__rdtsc()` | 8.3 ns |
| `__rdtscp(&aux)` | 11.1 ns |

`rdtsc` reads the CPU's **Time Stamp Counter**, a 64-bit counter that increments at a fixed rate. On every modern x86
CPU it's **invariant**: it ticks at a constant frequency regardless of the core's actual clock speed, power state or
turbo (look for `constant_tsc` and `nonstop_tsc` in `/proc/cpuinfo`), and it's synchronized across cores on a
socket. On the test machine it runs at 2.496 GHz.

```cpp
#include <x86intrin.h>

std::uint64_t t0 = __rdtsc();
handle(msg);
std::uint64_t t1 = __rdtsc();
double ns = (t1 - t0) / tsc_ghz;        // tsc_ghz calibrated at start-up
```

Calibrate the frequency once at start-up against `steady_clock`:

```cpp
double calibrate_tsc_ghz() {
    auto w0 = std::chrono::steady_clock::now();
    std::uint64_t r0 = __rdtsc();
    while (std::chrono::steady_clock::now() - w0 < std::chrono::milliseconds(200)) {}
    std::uint64_t r1 = __rdtsc();
    auto w1 = std::chrono::steady_clock::now();
    return (r1 - r0) / std::chrono::duration<double, std::nano>(w1 - w0).count();
}
```

### Ordering

`rdtsc` isn't a serializing instruction: out-of-order execution (Chapter 5.1) can execute it before earlier
instructions finish, or later instructions can start before it. For measuring something that takes microseconds
this doesn't matter. For tens of nanoseconds it does:

- `rdtscp` waits until all previous instructions have executed before reading the counter (but later instructions
  can still start early). Use it for the **end** timestamp.
- `lfence; rdtsc` prevents both reorderings around the read and is commonly used for the **start** timestamp.

Even so, the measurement itself costs ~10 ns. In the `push_back` table above, the median of ~13 ns is mostly the
cost of `rdtscp`; the push itself is a couple of nanoseconds. Subtract a measured baseline (time an empty region) or,
better, measure operations long enough that the overhead doesn't matter.

> [!NOTE]
> In production, trading systems timestamp events with the TSC at key points (packet received, decoded, decision
> made, order sent) and compute stage latencies from the differences. Wire-to-wire latency (from the packet arriving
> at the NIC to the order leaving it) is measured externally, with hardware timestamps from the NIC or a network tap,
> because no software clock can see time spent in the NIC and kernel.

---

## 3. Recording cheaply: histograms

Storing every sample and sorting at the end works for a benchmark, but not in production: millions of samples per
second would use too much memory and the recording itself would pollute the cache. Instead, record into a
**histogram**: an array of counters, one per latency bucket.

```cpp
class LinearHistogram {                         // 1 ns buckets up to 10 µs, plus an overflow bucket
public:
    void record(std::uint64_t ns) { ++counts_[std::min<std::uint64_t>(ns, kMax)]; ++n_; }
    std::uint64_t percentile(double q) const {
        std::uint64_t target = static_cast<std::uint64_t>(q * n_), seen = 0;
        for (std::size_t i = 0; i <= kMax; ++i)
            if ((seen += counts_[i]) > target) return i;
        return kMax;
    }
private:
    static constexpr std::size_t kMax = 10'000;
    std::array<std::uint64_t, kMax + 1> counts_{};
    std::uint64_t n_ = 0;
};
```

Recording is one increment: a couple of nanoseconds, no allocation. But linear buckets waste resolution: you need
1 ns resolution near 100 ns, while near 10 ms, ±1% is plenty. And 1 ns buckets up to 10 ms would be 10 million
counters (80 MB).

### Log-linear (HDR) histograms

The standard solution, popularized by Gil Tene's **HdrHistogram**, uses buckets whose width grows with the value,
keeping a fixed **relative** precision. The value's exponent (highest set bit) selects a group, and the next few
bits subdivide that group linearly:

```cpp
// 2^kSubBits linear sub-buckets per power of two: relative error < 1/2^kSubBits
constexpr int kSubBits = 5;                                         // 32 sub-buckets: ~3% precision

inline std::size_t bucket_index(std::uint64_t v) {
    if (v < (1u << kSubBits)) return v;                              // small values: exact
    int msb = 63 - std::countl_zero(v);                              // position of highest set bit
    int shift = msb - kSubBits;
    std::uint64_t sub = (v >> shift) & ((1u << kSubBits) - 1);       // next kSubBits bits after the msb
    return ((shift + 1) << kSubBits) + sub;
}
```

With 32 sub-buckets per power of two, values from 1 ns to 2⁴⁰ ns (about 18 minutes) fit in 1,152 counters
(9 KB) at ~3% precision. Recording is a `lzcnt`, a couple of shifts and an increment. The practice problem
"Latency Histogram with Percentiles" builds exactly this.

Histograms also **merge** trivially (add the counters), so each thread can keep its own, with no sharing on the hot
path, and a monitoring thread can combine them periodically.

### Exercises

:::exercise Bucket count
With `kSubBits = 5` and values up to 2⁴⁰, how many buckets does the log-linear scheme need? Why does adding one bit of
`kSubBits` double the bucket count but halve the relative error?
:::solution
Values below 32 use 32 exact buckets. Each power of two from 2⁵ up to 2⁴⁰ (35 more groups) gets 32 sub-buckets:
32 + 35 × 32 = 1,152 buckets, and the largest index `bucket_index` returns is 1,151. Each extra sub-bit
splits every group's 32 sub-buckets into 64, so twice the counters; the width of each bucket relative to its value
halves, so the worst-case relative error halves too.
:::

---

## 4. Coordinated omission

There's a subtle but devastating mistake in many load tests. Suppose a test client sends a request, waits for the
response, records the latency, then sends the next request, aiming for one request per millisecond:

```cpp
for (;;) {
    auto t0 = now();
    send_and_wait(request);
    record(now() - t0);
    sleep_until(t0 + 1ms);
}
```

Now the server stalls for 500 ms once. The client records **one** slow sample of 500 ms. But during that stall, 499
other requests *should* have been sent and would all have seen long delays (from 499 ms down to 1 ms). The client
didn't send them because it was waiting. The system under test effectively told the load generator to stop
measuring while it was slow. Gil Tene named this **coordinated omission**.

The result: the measured p99.9 can be orders of magnitude better than what real users (or real market data, which
doesn't wait for you) experience.

Fixes:

- **Measure from the intended send time**, not the actual one: `record(now() - intended_start)`, where
  `intended_start` advances by exactly 1 ms per request regardless of stalls. HdrHistogram's
  `recordValueWithExpectedInterval` back-fills the missing samples statistically.
- **Use an open-loop load generator**: send requests on schedule, independently of responses (multiple outstanding
  requests, or a separate sender thread).
- In trading, **replay market data at its recorded timestamps** and measure each message's latency from its
  scheduled time. A burst of 500 messages in a millisecond after an announcement must be replayed as a burst, and
  queueing delay counts as latency.

> [!QUANT]
> Market data is inherently open loop: the exchange doesn't slow down because your feed handler is busy. During a
> burst, messages queue in the NIC and socket buffers, and *queueing delay* dominates latency. A benchmark that feeds
> one message, waits for it to be processed, then feeds the next measures service time only, and will badly
> understate tail latency under real bursts.

---

## Summary

- Latency distributions are skewed; the mean and median hide the tail. Report p50, p99, p99.9, p99.99 and max, with
  sample counts.
- Most low-latency engineering is about removing tail sources: reallocation, page faults, allocation, locks,
  syscalls, cache misses.
- `rdtsc` (~8 ns) and `rdtscp` (~11 ns) are much cheaper than `steady_clock::now()` (~22 ns). Use the invariant TSC,
  calibrate it, and mind ordering for very short intervals.
- Record into histograms. Log-linear (HDR) histograms give fixed relative precision over a huge range in a few KB,
  and merge trivially.
- Avoid coordinated omission: measure from intended start times and drive load open loop. Replay market data at its
  real timing.

## Key terms

**Tail latency** · **percentile** · **p99 / p99.9** · **time stamp counter (TSC)** · **invariant TSC** ·
**`rdtscp`** · **calibration** · **histogram** · **log-linear (HDR) histogram** · **relative precision** ·
**coordinated omission** · **open-loop load** · **service time vs queueing delay**
