+++
title = "Time with std::chrono"
minutes = 35
practice_tags = ["measuring", "finance"]
+++

## Why this chapter matters

Three timing bugs, each of which has happened in real trading systems:

- A latency monitor computes `end - start` using wall-clock time. An NTP adjustment steps the clock back 40 ms in
  the middle of a measurement, and the dashboard shows a negative latency. Another day a step forward shows a
  40 ms spike that never happened.
- A function takes a timeout as `int timeout`. One caller passes milliseconds, another passes microseconds. The
  first caller's orders time out 1,000× too late.
- A backtest stores exchange timestamps as `double` seconds since 1970. At today's epoch values a `double` has
  about 0.2 µs of resolution, so nanosecond timestamps are silently rounded and two events in the same microsecond
  can swap order.

Time is a core quantity in trading: timestamps on every message, latency budgets in nanoseconds, session schedules,
timeouts. `std::chrono` gives time a type system: durations that carry their units, time points tied to a specific
clock, and conversions that are exact or explicit. This chapter covers how to use it, and how to measure elapsed
time correctly.

By the end of this chapter you should be able to:

- use `std::chrono::duration` with unit-safe arithmetic and conversions;
- choose between `system_clock`, `steady_clock` and `high_resolution_clock`;
- represent exchange timestamps as integer nanoseconds without loss;
- measure elapsed time correctly, and know the cost and resolution of reading a clock.

---

## 1. Durations

A `std::chrono::duration<Rep, Period>` is a count of ticks (`Rep`, usually an integer) with a compile-time unit
(`Period`, a `std::ratio` of seconds):

```cpp
using namespace std::chrono_literals;

std::chrono::nanoseconds  a = 250ns;      // duration<int64_t, std::nano>
std::chrono::microseconds b = 3us;
std::chrono::milliseconds c = 5ms;
auto total = a + b + c;                  // nanoseconds: 5'003'250ns. Mixed units convert automatically and exactly.

std::printf("%lld ns\n", static_cast<long long>(total.count()));
```

The rules:

- **Implicit conversion is allowed only when it's exact**: milliseconds → nanoseconds is fine (multiply by
  1,000,000), nanoseconds → milliseconds is not (it would truncate).
- **Lossy conversion must be explicit** with `std::chrono::duration_cast` (truncates toward zero), `floor`, `ceil`
  or `round`.
- Arithmetic between different units produces the finer unit.

```cpp
std::chrono::milliseconds ms = 1500us;                                        // error: would lose precision
auto ms2 = std::chrono::duration_cast<std::chrono::milliseconds>(1500us);    // 1ms (truncated)
auto ms3 = std::chrono::round<std::chrono::milliseconds>(1500us);            // 2ms
```

With durations in interfaces, the "is this timeout in ms or µs?" bug becomes impossible:

```cpp
void set_timeout(std::chrono::nanoseconds timeout);   // accepts any exactly-convertible duration

set_timeout(5ms);     // converted exactly to 5'000'000ns
set_timeout(250us);
set_timeout(5);       // error: an int has no unit
```

**Cost: none.** A `std::chrono::nanoseconds` is an `int64_t` in a wrapper. Conversions between units are
compile-time constant multiplications. The type system does the bookkeeping for free.

> [!NOTE]
> `std::chrono::nanoseconds` uses a 64-bit signed count, which covers about ±292 years. Plenty for timestamps
> since 1970 and for any latency. Coarser standard units use fewer bits but still cover at least ±292 years.

### Exercises

:::exercise Which compile?
Which lines compile? (a) `std::chrono::seconds s = 2min;` (b) `std::chrono::minutes m = 90s;` (c)
`std::chrono::microseconds u = 1ms + 1ns;` (d) `auto d = 1s / 3;` (e) `double r = 1ms / 1us;`
:::solution
(a) Yes: minutes → seconds is exact. (b) No: 90 seconds isn't a whole number of minutes; needs `duration_cast`
(or `floor`). (c) No: `1ms + 1ns` is nanoseconds (1,000,001ns), which can't convert exactly to microseconds.
(d) Yes: integer division of a duration by a number gives `seconds{0}` here, since `Rep` is an integer. Use
`std::chrono::duration<double>` for fractional seconds. (e) Yes: dividing two durations gives a plain number
(1000), the ratio of the two.
:::

---

## 2. Clocks and time points

A **clock** provides `now()`, which returns a `time_point<Clock, Duration>`: a duration since that clock's
**epoch**. Time points from different clocks are different types and can't be mixed.

| Clock | Epoch | Properties | Use for |
|---|---|---|---|
| `system_clock` | 1970-01-01 UTC (since C++20, guaranteed) | wall-clock time; **can jump** forward or backward (NTP, manual changes) | timestamps meant to be compared across machines, logs, matching exchange times |
| `steady_clock` | unspecified (often boot time) | **monotonic**: never goes backward; ticks at a steady rate | measuring elapsed time, timeouts, intervals |
| `high_resolution_clock` | — | an alias for one of the two above (libstdc++: `system_clock`) | don't use it; pick one of the others explicitly |

```cpp
auto start = std::chrono::steady_clock::now();
do_work();
auto elapsed = std::chrono::steady_clock::now() - start;          // a duration
auto us = std::chrono::duration_cast<std::chrono::microseconds>(elapsed).count();
```

The rule is simple: **measure intervals with `steady_clock`; record "when" with `system_clock`.** Measuring with
`system_clock` gives the negative latencies from the introduction whenever NTP steps the clock.

> [!WARNING]
> `high_resolution_clock` sounds like the right choice for benchmarks, but in libstdc++ it's `system_clock`, which
> isn't monotonic. `steady_clock` on Linux has the same nanosecond resolution and is monotonic. Use `steady_clock`.

### Calendar types (C++20)

C++20 added dates, time zones and formatting to `<chrono>`:

```cpp
using namespace std::chrono;
year_month_day today = floor<days>(system_clock::now());
auto ny_open = zoned_time{"America/New_York", local_days{today} + 9h + 30min};
std::cout << std::format("{:%F %T %Z}\n", ny_open);   // e.g. 2026-11-11 09:30:00 EST
```

Session schedules, holiday calendars and "what time is the London close in New York today" become exact,
time-zone-correct code. Calendar and time zone support arrived in GCC 14 and recent libc++; on older compilers,
Howard Hinnant's `date` library provides the same API.

---

## 3. Timestamps in trading systems

Exchanges publish timestamps in nanoseconds, typically as an integer count since midnight or since the Unix epoch.
Store them the same way: a 64-bit integer count of nanoseconds, never a `double`.

```cpp
using Nanos     = std::chrono::nanoseconds;
using Timestamp = std::chrono::time_point<std::chrono::system_clock, Nanos>;   // exact ns since 1970 UTC

Timestamp from_exchange(std::uint64_t ns_since_epoch) {
    return Timestamp{Nanos{static_cast<std::int64_t>(ns_since_epoch)}};
}
```

Why not `double` seconds? Around 1.8 × 10⁹ seconds since 1970, adjacent `double` values are about 2.4 × 10⁻⁷ s apart,
roughly 0.24 µs. Nanosecond detail is lost the moment it's stored, and arithmetic on such values accumulates
rounding error. An `int64_t` count of nanoseconds is exact, compares in one instruction, and sorts correctly.

Many systems use a plain `std::int64_t` or `std::uint64_t` for timestamps in hot structures and wire formats (where
layout must be controlled exactly) and wrap them in `chrono` types at the interfaces, which keeps both benefits.

> [!QUANT]
> In a backtester, keep **two** notions of time apart: the *event time* carried in the market data (exchange
> timestamps, which drive the simulation) and the *wall time* of the machine running the backtest. Strategies must
> only see event time, through an injected clock policy (Chapter 3.6). Calling `system_clock::now()` inside
> strategy code is a classic source of non-reproducible backtests.

---

## 4. Measuring elapsed time correctly

Reading a clock isn't free, and its resolution isn't infinite:

- `steady_clock::now()` on Linux x86-64 calls `clock_gettime(CLOCK_MONOTONIC)` through the **vDSO**: no system call,
  just reading the CPU's timestamp counter and scaling it. It costs about **15–25 ns**.
- Its resolution is 1 ns in the type, but its *accuracy* is limited by the TSC frequency and the read itself.
  Measuring anything that takes less than about a microsecond with a single pair of `now()` calls is mostly
  measuring the clock.
- The raw timestamp counter (`__rdtsc()`, or `rdtscp`) costs about 5–10 ns and counts CPU reference cycles. It's
  what latency-critical systems use for internal timestamps, converted to nanoseconds with a calibrated frequency.
  Chapter 6.4 covers it in detail, including serialization and frequency calibration.

```cpp
template <class F>
std::chrono::nanoseconds time_it(F&& f, int iterations) {
    auto start = std::chrono::steady_clock::now();
    for (int i = 0; i < iterations; ++i) f();
    auto end = std::chrono::steady_clock::now();
    return (end - start) / iterations;     // average per call; clock overhead amortized
}
```

Timing many iterations amortizes the clock overhead, but averages hide the tail. For latency you need the
distribution of individual measurements (percentiles, histograms), which Chapter 6.4 builds. And for
microbenchmarks you also have to stop the compiler from optimizing the work away (Chapter 6.1).

### Exercises

:::exercise What's wrong with this benchmark?
```cpp
auto t0 = std::chrono::high_resolution_clock::now();
double r = std::sqrt(x);
auto t1 = std::chrono::high_resolution_clock::now();
std::printf("%f ns\n", std::chrono::duration<double, std::nano>(t1 - t0).count());
```
List three problems.
:::solution
(1) `high_resolution_clock` is `system_clock` in libstdc++: not monotonic, so an NTP adjustment can corrupt the
result. Use `steady_clock`. (2) One `sqrt` takes a few nanoseconds, far less than the ~20 ns cost of a clock read,
so the output measures the clock, not `sqrt`. Time many iterations. (3) `r` is never used, so the compiler can
delete the `sqrt` entirely, or move it outside the timed region; nothing in the code orders it relative to the
clock reads. Use the result (or a "do not optimize" barrier, Chapter 6.1).
:::

---

## Summary

- `std::chrono::duration` carries its unit in the type. Exact conversions are implicit; lossy ones need
  `duration_cast`, `floor`, `ceil` or `round`. It costs nothing at run time.
- Put durations in interfaces so unit mistakes can't compile.
- `system_clock` is wall time and can jump; `steady_clock` is monotonic. Measure intervals with `steady_clock`.
  Avoid `high_resolution_clock`.
- Store timestamps as 64-bit integer nanoseconds, never `double` seconds.
- Keep event time and wall time separate in backtests.
- `steady_clock::now()` costs ~20 ns via the vDSO; for anything shorter, time many iterations or use the TSC.

## Key terms

**Duration** · **tick period** · **`duration_cast`** · **time point** · **epoch** · **clock** ·
**monotonic clock** · **`system_clock`** · **`steady_clock`** · **vDSO** · **timestamp counter (TSC)** ·
**event time vs wall time**
