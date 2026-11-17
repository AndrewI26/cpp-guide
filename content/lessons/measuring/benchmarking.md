+++
title = "Microbenchmarking Without Lying to Yourself"
minutes = 45
practice_tags = ["measuring"]
+++

## Why this chapter matters

Here's a benchmark of a simple payoff function:

```cpp
double payoff(double s, double k) { return std::max(s - k, 0.0) * std::exp(-0.01); }

auto t0 = std::chrono::steady_clock::now();
for (int i = 0; i < 10'000'000; ++i) {
    double r = payoff(prices[i & 1023], 100.0);
    (void)r;
}
auto t1 = std::chrono::steady_clock::now();
```

On the test machine at `-O2` it reports **0.000 ns per call**. The function isn't infinitely fast: the compiler saw
that `r` is never used, deleted the call, and then deleted the empty loop. Adding one line that forces the result to
be "used" gives **1.12 ns per call**, the real cost.

Microbenchmarks are how you decide between two implementations, check that an optimization worked, and build
intuition about costs. They're also extraordinarily easy to get wrong, in ways that produce confident, precise,
meaningless numbers. The optimizer, the CPU's caches and predictors, frequency scaling and the OS all conspire
against you. This chapter is about the discipline that makes the numbers trustworthy.

By the end of this chapter you should be able to:

- prevent the compiler from optimizing away or hoisting the code under test;
- control for warm-up, cache state, branch-predictor training and frequency scaling;
- use repetitions and robust statistics to deal with noise;
- write benchmarks with Google Benchmark;
- recognize when a microbenchmark can't answer your question.

---

## 1. The optimizer is not on your side

The compiler's job is to produce code with the same *observable behaviour* as your source, as fast as possible.
Time isn't observable behaviour. A benchmark whose results aren't used has no observable effect, so the compiler is
entitled to delete it, and at `-O2` it usually does. The traps:

- **Dead-code elimination**: the result is unused, so the computation disappears (the introduction's 0.000 ns).
- **Constant folding**: the inputs are compile-time constants, so the compiler computes the answer at compile time.
  `payoff(105.0, 100.0)` in a loop is just a constant.
- **Loop-invariant hoisting**: the same computation every iteration is done once, outside the loop.
- **Vectorization and strength reduction** you didn't intend: the benchmark loop may be optimized in ways the real
  call site won't be.
- **Inlining into the benchmark**: the function is specialized for the benchmark's exact arguments.

### Escaping values

The standard fix is a compiler barrier that makes a value appear to be used, without generating any real
instructions. Google Benchmark calls it `DoNotOptimize`; a minimal version for GCC and Clang:

```cpp
template <class T>
inline void do_not_optimize(const T& value) {
    asm volatile("" : : "r,m"(value) : "memory");   // "the value is read by unknown code"
}

inline void clobber_memory() {
    asm volatile("" : : : "memory");                 // "any memory may have been read or written"
}
```

The empty `asm volatile` statement emits nothing, but the compiler must assume it reads `value` (so `value` must be
computed) and, with the `"memory"` clobber, that it may read or write any memory (so stores before it can't be
deleted and loads after it can't be hoisted).

Use them to **consume results** and to **hide inputs** from constant propagation:

```cpp
double s = 105.0, k = 100.0;
for (int i = 0; i < n; ++i) {
    do_not_optimize(s);                // the compiler can no longer assume s is 105.0
    double r = payoff(s, k);
    do_not_optimize(r);                // r must be computed
}
```

> [!WARNING]
> Barriers can also *pessimize*: forcing a value to memory or a register each iteration can block vectorization or
> add work that doesn't exist in real code. A barrier should mark the boundary of the measured operation, and you
> should check the generated assembly (Chapter 6.3) to see what was actually measured.

### Exercises

:::exercise What will this measure?
What does this benchmark actually measure at `-O2`?

```cpp
std::vector<int> v(1'000'000, 1);
auto t0 = now();
long s = 0;
for (int rep = 0; rep < 100; ++rep)
    for (int x : v) s += x;
auto t1 = now();
std::printf("%ld\n", s);
```
:::solution
The result is used (printed), so the loops won't be deleted. But the compiler can see that every element is 1 only
if it can track the vector's contents, which it generally can't across the allocation, so the inner loop survives
and is vectorized. It may, however, notice that each repetition adds the same total and compute the inner sum once
and multiply by 100 (loop-invariant reasoning on the outer loop). Then you're measuring one pass, not 100. The only
way to know is to read the assembly, or to make each repetition depend on something the compiler can't predict, for
example `clobber_memory()` between repetitions.
:::

---

## 2. The machine is not on your side either

Even with the compiler under control, the hardware and OS add effects that can swamp what you're measuring.

**Warm-up.** The first run of a piece of code pays for page faults (Chapter 5.5), instruction cache misses, empty
branch predictors and cold data caches. Run the code several times before measuring, or report steady state and
first-run separately if both matter. (For trading, both do: the first order of the day pays all of them; Chapter
12.2.)

**Cache state.** A benchmark that runs the same function on the same 1 KB of input a million times measures
L1-resident performance. If the real code sees fresh data each call, the benchmark is optimistic by up to 100×
(Chapter 5.2). Size the input to match reality: use a working set larger than the cache, or deliberately evict.

**Branch predictor training.** A benchmark that repeats the same input pattern lets the predictor learn it
(Chapter 5.4 measured predictors learning any pattern up to period 64). Use realistic, varied inputs; random data
from a fixed seed is a good default.

**Frequency scaling.** Modern CPUs change their clock speed with load, temperature and power limits. A benchmark
may start at turbo frequency and slow down after a few seconds, or run at a low frequency because the core was
idle. On a benchmark machine: set the CPU governor to `performance`, and consider disabling turbo for consistent
results. (The test machine's timestamp counter runs at a constant 2.50 GHz regardless of the actual core frequency,
which is why cycle estimates in this book are approximate.)

**Noise.** Other processes, interrupts, the OS scheduler moving your thread, and SMT siblings all add variance. Pin
the benchmark to an isolated core (`taskset -c 3 ./bench`), close other programs, and run several repetitions.

| Effect | Symptom | Control |
|---|---|---|
| Dead code / constant folding | impossibly fast | `do_not_optimize`, hide inputs, read the assembly |
| Cold start | first iteration slow | warm-up runs |
| Unrealistic cache residency | much faster than production | realistic working-set size |
| Predictor trained on toy data | branchy code looks great | realistic, randomized inputs |
| Frequency scaling | drift over time, results vary between runs | `performance` governor, fixed frequency |
| OS noise | outliers, high variance | pinning, isolation, repetitions, robust statistics |

---

## 3. Statistics: don't trust one number

Every measurement has noise, and the noise isn't symmetric: interference only ever makes things **slower**. So:

- **Repeat** the measurement many times.
- For a **throughput** question ("which implementation is faster?"), report the **minimum** or the **median** of
  the repetitions. The minimum is the closest estimate of the cost without interference; the median is more robust
  if the minimum is a fluke.
- Report the **spread** (e.g. min, median, max, or the interquartile range). If two implementations' ranges
  overlap heavily, you haven't shown a difference.
- For a **latency** question ("how long does each call take?"), the average is the wrong statistic entirely: you
  need the distribution of individual measurements and its tail percentiles. That's Chapter 6.4.

A helper used throughout this book's benchmarks:

```cpp
template <class F>
double best_of_ns(F&& f, int reps = 7) {
    double best = 1e300;
    for (int r = 0; r < reps; ++r) {
        auto t0 = std::chrono::steady_clock::now();
        f();
        auto t1 = std::chrono::steady_clock::now();
        best = std::min(best, std::chrono::duration<double, std::nano>(t1 - t0).count());
    }
    return best;
}
```

Each `f()` should run long enough (at least a millisecond, ideally more) that the ~20 ns cost of reading the clock
(Chapter 4.7) is negligible.

> [!TIP]
> Changes of less than a few percent are very hard to measure reliably on a desktop machine. Before concluding that
> version B is 3% faster, run A and B alternately several times. If the ordering flips between runs, the difference
> is noise.

---

## 4. Google Benchmark

Writing timing loops by hand gets old quickly. **Google Benchmark** is the de facto standard library for C++
microbenchmarks. It picks iteration counts automatically, runs repetitions, reports statistics, and provides
`DoNotOptimize` and `ClobberMemory`.

```cpp bench_payoff.cpp
#include <benchmark/benchmark.h>
#include <cmath>
#include <random>
#include <vector>

double payoff(double s, double k) { return std::max(s - k, 0.0) * std::exp(-0.01); }

static void BM_Payoff(benchmark::State& state) {
    std::vector<double> spots(4096);
    std::mt19937_64 rng(42);
    std::uniform_real_distribution<double> d(80.0, 120.0);
    for (double& s : spots) s = d(rng);

    std::size_t i = 0;
    for (auto _ : state) {                         // the timed loop
        double r = payoff(spots[i++ & 4095], 100.0);
        benchmark::DoNotOptimize(r);
    }
    state.SetItemsProcessed(state.iterations());
}
BENCHMARK(BM_Payoff);

static void BM_SumVector(benchmark::State& state) {
    std::vector<int> v(state.range(0), 1);
    for (auto _ : state) {
        long s = 0;
        for (int x : v) s += x;
        benchmark::DoNotOptimize(s);
    }
    state.SetBytesProcessed(state.iterations() * state.range(0) * sizeof(int));
}
BENCHMARK(BM_SumVector)->RangeMultiplier(8)->Range(1 << 10, 1 << 24);   // sweep working-set sizes

BENCHMARK_MAIN();
```

```bash
g++ -std=c++20 -O2 -march=native bench_payoff.cpp -lbenchmark -lpthread -o bench
./bench --benchmark_repetitions=10 --benchmark_report_aggregates_only=true
```

Sweeping a size parameter, as `BM_SumVector` does, is one of the most informative things you can do: the result
shows each cache level as a step, exactly like Chapter 5.2's latency curve.

---

## 5. When microbenchmarks mislead

Even a technically perfect microbenchmark answers a narrow question: how fast is this code, in isolation, with this
input, on this machine, with a warm cache? Production differs:

- **Context.** In the real program, other code runs between calls and evicts your data and code from cache. A hash
  table that's fastest in isolation may be slowest in situ because it uses more cache.
- **Inlining and specialization.** The benchmark may inline and specialize in ways the real call site can't.
- **Input distribution.** Real market data has bursts, skew and correlations that uniform random inputs don't.

The remedy is to treat microbenchmarks as one tool among several: use them to compare candidates and understand
costs, then confirm with a **macro benchmark** (replaying real market data through the whole system) and with
profiling of the real program (Chapter 6.2).

### Exercises

:::exercise Review this benchmark
A colleague claims their new order-book `add` is 40% faster, based on: a loop that adds 1,000 orders at the same
price to an empty book, timed once with `system_clock`, at `-O0`. List every problem.
:::solution
(1) `-O0`: meaningless; abstractions aren't inlined (Chapter 1.2). (2) Timed once: no repetitions, no statistics,
no warm-up. (3) `system_clock` isn't monotonic (Chapter 4.7); use `steady_clock`. (4) All orders at one price:
exercises one level only, trains the branch predictor and keeps everything in L1; real books have many levels and
mixed operations. (5) An empty book that grows to 1,000 orders: measures growth and first-touch allocation, not
steady-state behaviour. (6) No check that results are used. (7) Only `add` is measured; a faster `add` that slows
`cancel` or makes the structure bigger may be a net loss. Rebuild with realistic replayed operations, `-O2`,
repetitions, and a comparison run of the old version in the same harness.
:::

---

## Summary

- The compiler deletes, folds and hoists unused or predictable work. Consume results and hide inputs with
  `do_not_optimize`, and check the assembly.
- Control warm-up, working-set size, branch-predictor training, CPU frequency and OS noise.
- Repeat measurements; report minimum or median plus spread for throughput. Use distributions, not averages, for
  latency.
- Google Benchmark handles iteration counts, repetitions and statistics; sweep sizes to see cache effects.
- Microbenchmarks answer narrow questions. Confirm conclusions with full-system replays and profiles.

## Key terms

**Microbenchmark** · **dead-code elimination** · **constant folding** · **loop-invariant hoisting** ·
**compiler barrier** · **`DoNotOptimize`** · **warm-up** · **frequency scaling** · **noise** · **minimum / median** ·
**macro benchmark**
