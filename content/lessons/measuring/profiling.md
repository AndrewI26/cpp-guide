+++
title = "Profiling with perf and Flame Graphs"
minutes = 40
practice_tags = ["measuring"]
+++

## Why this chapter matters

A backtester takes 40 minutes to replay a month of data. The team is sure the slow part is the strategy's signal
computation, and spends a week vectorizing it. The backtester now takes 39 minutes. A profile, which takes five
minutes to collect, would have shown that 60% of the time was in `std::unordered_map::find` inside the order-book
code and 15% in `strtod` parsing timestamps from text, and the signal computation was 4%.

Intuition about where a program spends its time is wrong surprisingly often, even for experienced engineers.
A **profiler** tells you. On Linux, the standard tool is **perf**, which can sample where your program is executing
and read the CPU's **hardware performance counters** to tell you *why* it's slow: cache misses, branch mispredicts,
stalls. This chapter covers the workflow from "it's slow" to "this line, because of this".

By the end of this chapter you should be able to:

- explain how a sampling profiler works and what its output means;
- use `perf stat` to measure hardware events and derive IPC, miss rates and mispredict rates;
- use `perf record` and `perf report` to find hot functions and lines, with call graphs;
- read a flame graph;
- choose the right build flags for profiling.

---

## 1. Sampling profilers

A **sampling profiler** interrupts the program at regular intervals (say, 4,000 times a second), records the
current instruction address and, optionally, the call stack, then lets it continue. After enough samples, the
fraction of samples landing in each function approximates the fraction of time spent there.

Sampling has very low overhead (a few percent) and needs no changes to the program, so you can profile optimized
production builds on real workloads. Its limitations:

- It's **statistical**: a function that takes 0.1% of the time may show up as 0 or 3 samples. Collect enough samples
  (thousands at least) for the hot spots to be clear.
- It shows where time is spent, not how many times something was called. A function called once that takes a
  second and one called a billion times taking a nanosecond each look the same.
- **Skid**: on out-of-order CPUs the recorded address can be a few instructions after the one actually responsible.
  At the level of functions and loops it doesn't matter; at the level of single instructions, keep it in mind.

The alternative, **instrumentation** (adding timing code to every function, as `gprof` or manual timers do),
distorts small functions heavily and changes inlining. For performance work, sampling is the default.

---

## 2. `perf stat`: counting events

`perf stat` runs a program and counts hardware and software events:

```bash
perf stat -e cycles,instructions,branches,branch-misses,L1-dcache-load-misses,LLC-load-misses ./backtest
```

Example output (abbreviated, illustrative):

```text
    41,203,118,442      cycles
    52,977,610,530      instructions              #    1.29  insn per cycle
     9,880,455,301      branches
       412,330,912      branch-misses             #    4.17% of all branches
     2,104,778,105      L1-dcache-load-misses
       388,120,553      LLC-load-misses

      16.48 seconds time elapsed
```

The derived numbers tell you what kind of problem you have:

| Metric | How to read it |
|---|---|
| **IPC** (instructions per cycle) | Above ~2: compute-bound and running well; optimize the algorithm or vectorize. Below ~1: the core is waiting, usually on memory or mispredicts. |
| **Branch miss rate** | Below ~1% is good. Several percent on hot code means unpredictable branches (Chapter 5.4): roughly 20 cycles each. |
| **LLC (last-level cache) misses** | Each is a trip to DRAM, ~100 ns. Multiply by 100 ns and compare with total run time to see how much of it is memory latency. |

In the example: 388 million LLC misses × ~100 ns is about 39 seconds of miss latency if none overlapped. The run
took 16 seconds, so misses overlap somewhat, but memory is clearly the dominant cost, consistent with an IPC of
1.29. That points straight at data structures and layout (Chapter 7), not arithmetic.

> [!NOTE]
> Access to hardware counters is controlled by `/proc/sys/kernel/perf_event_paranoid`. A value of 2 or less lets
> ordinary users profile their own processes; some distributions default to higher values (4 on recent Ubuntu,
> which disables perf for non-root users entirely). On a development box, `sudo sysctl kernel.perf_event_paranoid=1`
> enables it.

For a more systematic breakdown, `perf stat --topdown` (on recent Intel CPUs) classifies cycles into front-end
bound, back-end bound (memory or core), bad speculation and retiring, which is Intel's **Top-Down Microarchitecture
Analysis** method.

### Exercises

:::exercise Diagnose from counters
A hot loop shows IPC 0.4, a 0.2% branch miss rate and a very high LLC miss count. Another shows IPC 0.9, a 12% branch
miss rate and few cache misses. What's the likely bottleneck in each, and what would you try?
:::solution
The first is memory-latency bound: predictable control flow but the core is stalled on DRAM. Try improving
locality (smaller structures, contiguous layout, hot/cold splitting), prefetching, or batching independent lookups
so misses overlap. The second is bound by branch mispredictions: 12% of branches cost ~20 cycles each. Find the
unpredictable branch (`perf record -e branch-misses`) and make it branchless, or reorganize the data so it becomes
predictable.
:::

---

## 3. `perf record` and `perf report`: finding hot spots

To see *where* time goes, record samples, then browse them:

```bash
perf record -g ./backtest          # -g: also record call stacks
perf report                        # interactive: functions sorted by sample count
```

Example `perf report` view (illustrative):

```text
  Children      Self  Command   Symbol
+   71.20%     0.40%  backtest  [.] Engine::on_message
+   38.10%    31.85%  backtest  [.] std::_Hashtable<...>::find
+   16.02%    15.11%  backtest  [.] __strtod_internal
+    9.33%     9.33%  backtest  [.] OrderBook::add
+    4.20%     3.98%  backtest  [.] Strategy::compute_signal
```

- **Self** is time spent in the function's own code.
- **Children** includes everything it calls.

Pressing `a` on a function in `perf report` (or running `perf annotate`) shows its assembly, interleaved with source
lines if debug info is present, with the percentage of samples on each instruction. That's where you see "40% of
this function's samples are on the load right after the hash bucket lookup", meaning a cache miss.

You can also sample on specific events to find *where* they happen:

```bash
perf record -e branch-misses ./backtest      # samples land on the mispredicting branches
perf record -e LLC-load-misses ./backtest    # samples land on the loads that miss to DRAM
```

### Build flags for profiling

- **Optimize as in production** (`-O2` or whatever you ship). Profiling a debug build finds problems that don't
  exist in production (Chapter 1.2).
- **Add `-g`** for symbols and line numbers. It doesn't change the generated code.
- **Add `-fno-omit-frame-pointer`** so `perf record -g` can walk the stack cheaply and reliably. The cost is
  typically about 1%, and many companies (and some Linux distributions) now build everything this way. Without it,
  use `perf record --call-graph dwarf`, which works but produces much larger profiles.

> [!TIP]
> Inlined functions don't appear as separate entries in `perf report`; their samples are attributed to the caller.
> If a template-heavy function looks enormous, `perf annotate` with `-g` debug info shows which inlined source lines
> the samples fall on.

---

## 4. Flame graphs

For a big program, a list of functions loses the structure of *who called what*. A **flame graph** (Brendan Gregg's
visualization) shows all sampled call stacks at once:

```bash
perf record -F 999 -g ./backtest
perf script | stackcollapse-perf.pl | flamegraph.pl > flame.svg    # scripts from the FlameGraph repository
```

How to read one:

- The **x axis** is the fraction of samples, not time order. Wider boxes mean more time.
- The **y axis** is stack depth: each box's callers are below it, its callees above.
- **Wide plateaus at the top** are functions where time is spent directly. Those are your hot spots.
- A wide box with many narrow children means time is spread thinly; optimizing any one child won't help much.

```svg "A schematic flame graph of the backtester: the widest top-level boxes, the hash table lookup and strtod, are where time goes. Strategy::compute_signal, the part the team optimized, is a narrow sliver."
<svg viewBox="0 0 700 170" xmlns="http://www.w3.org/2000/svg" role="img"><rect class="box" x="10" y="130" width="680" height="28"/><text x="18" y="149">main → Engine::run</text><rect class="box" x="10" y="98" width="484" height="28"/><text x="18" y="117">Engine::on_message (71%)</text><rect class="box" x="500" y="98" width="150" height="28"/><text x="508" y="117">Feed::parse (24%)</text><rect class="f4" x="10" y="66" width="259" height="28"/><text x="18" y="85">unordered_map::find (38%)</text><rect class="f2" x="275" y="66" width="64" height="28"/><text x="281" y="85">Book::add 9%</text><rect class="f3" x="345" y="66" width="29" height="28"/><text class="mono" x="347" y="85">sig</text><rect class="f1" x="500" y="66" width="109" height="28"/><text x="508" y="85">strtod (16%)</text><text class="muted" x="345" y="50">compute_signal: 4%</text></svg>
```

Flame graphs are also useful for **comparison**: a differential flame graph of before and after a change shows
exactly which paths got faster or slower.

---

## 5. The profiling workflow

1. **Reproduce** the slow case with a realistic workload: replayed market data, not a toy input.
2. **`perf stat`** to classify the problem: low IPC (waiting) vs high IPC (computing), and which events are high.
3. **`perf record -g`** and a flame graph to find where the time goes.
4. **Annotate** the hottest functions to find the instructions responsible.
5. **Form a hypothesis** ("this lookup misses L3 because the table is 30 MB"), make one change, and **measure
   again** with the same workload. Keep a log of each change and its effect.
6. Stop when the profile is flat: no single thing dominates, and the remaining cost is the work you actually need
   to do.

> [!IMPORTANT]
> Profilers measure throughput-oriented "where does time go" questions well. Latency questions (why is the 99.9th
> percentile tick-to-trade 40 µs when the median is 3 µs?) need different tools: per-event timestamps and histograms
> (Chapter 6.4), and tracing of the rare slow events. A profile averages away exactly the outliers you care about.

---

## Summary

- Sampling profilers record where the program is executing at intervals; overhead is low and they work on optimized
  builds.
- `perf stat` counts hardware events. IPC, branch miss rate and LLC misses classify the bottleneck.
- `perf record -g` + `perf report`/`perf annotate` find hot functions and instructions. Sample on `branch-misses` or
  `LLC-load-misses` to find where those happen.
- Profile `-O2 -g -fno-omit-frame-pointer` builds with realistic workloads.
- Flame graphs show where time goes across all call stacks; wide plateaus at the top are hot spots.
- Profiles show averages. Tail latency needs per-event measurement.

## Key terms

**Sampling profiler** · **instrumentation** · **skid** · **hardware performance counter** · **`perf stat`** ·
**IPC** · **LLC miss** · **Top-Down analysis** · **`perf record`** · **`perf report`** · **`perf annotate`** ·
**self vs children** · **frame pointer** · **flame graph**
