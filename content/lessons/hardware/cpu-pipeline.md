+++
title = "Pipelines, Superscalar and Out-of-Order Execution"
minutes = 40
practice_tags = ["numerics", "measuring"]
+++

## Why this chapter matters

Here are two loops that sum the same 2,048 `double`s (16 KB, so the data sits in L1 cache the whole time):

```cpp
double sum1(const double* a, int n) {
    double s = 0;
    for (int i = 0; i < n; ++i) s += a[i];
    return s;
}

double sum4(const double* a, int n) {          // n is a multiple of 4
    double s0 = 0, s1 = 0, s2 = 0, s3 = 0;
    for (int i = 0; i < n; i += 4) {
        s0 += a[i]; s1 += a[i + 1]; s2 += a[i + 2]; s3 += a[i + 3];
    }
    return (s0 + s1) + (s2 + s3);
}
```

`sum4` does the same number of additions, plus a few more at the end. On the test machine (Intel Core i5-6500T,
Skylake, compiled with `-O2`), `sum1` takes **1.49 ns per element** and `sum4` takes **0.37 ns**: four times faster.
Nothing about memory explains it, since everything is in L1. The difference is in how the CPU core executes
instructions: `sum1` forces every addition to wait for the previous one, and `sum4` doesn't.

This chapter builds the mental model of a modern CPU core that the rest of Part II relies on: pipelines, multiple
execution units, out-of-order execution, and the distinction between **latency** and **throughput**.

By the end of this chapter you should be able to:

- explain pipelining and why an instruction's latency differs from its throughput;
- identify loop-carried dependency chains and break them with multiple accumulators;
- describe what out-of-order execution can and can't hide;
- use instruction latency and throughput figures to estimate the speed of a tight loop.

---

## 1. The pipeline

Executing an instruction takes several steps: **fetch** it from memory, **decode** it, read its operands, **execute**
it, and **write back** the result. A naive CPU would do all of these for one instruction before starting the next.

A **pipelined** CPU overlaps them, like an assembly line. While one instruction executes, the next is being decoded
and the one after that fetched. Each instruction still takes several cycles from start to finish, but the pipeline
can **complete** one instruction per cycle.

Modern x86 cores have pipelines of around 14–20 stages. Two consequences:

- **Latency and throughput are different things.** A floating-point add on Skylake has a **latency** of 4 cycles
  (its result is available 4 cycles after it starts) but a **throughput** of 2 per cycle (two independent adds can
  *start* every cycle, on two separate units).
- **Anything that stalls or flushes the pipeline is expensive.** A mispredicted branch throws away all the work
  already in flight, costing about 15–20 cycles (Chapter 5.4).

### Superscalar: several instructions per cycle

Modern cores are also **superscalar**: they have several execution units and can issue several instructions per
cycle. Skylake can decode and issue up to 4 (micro-)instructions per cycle, and has execution ports for:

| Unit | Count on Skylake | Example instructions |
|---|---|---|
| Integer ALU | 4 | `add`, `sub`, `and`, `cmp`, `lea` |
| Branch | 2 | `jcc`, `jmp` |
| Load | 2 | `mov reg, [mem]` |
| Store | 1 | `mov [mem], reg` |
| FP / SIMD add, multiply, FMA | 2 | `addsd`, `mulpd`, `vfmadd231pd` |
| Integer / FP divider | 1, not fully pipelined | `div`, `divsd` |

So a single core can, in principle, retire 4 instructions per cycle. Whether it actually does depends on whether the
instructions are **independent**.

---

## 2. Latency, throughput and dependency chains

Some approximate figures for Skylake (from Agner Fog's instruction tables and uops.info; other modern cores are
similar):

| Operation | Latency (cycles) | Throughput (per cycle) |
|---|---|---|
| integer `add`, `and`, `xor` | 1 | 4 |
| integer `imul` (64-bit) | 3 | 1 |
| integer `div` (64-bit) | 35–88 | ~1/25 |
| FP `add` / `mul` / FMA (`double`) | 4 | 2 |
| FP `div` (`double`) | 13–14 | 1/4 |
| FP `sqrt` (`double`) | 15–16 | 1/4–1/6 |
| L1 load | 4–5 | 2 |

Look at `sum1` again. Each iteration computes `s = s + a[i]`, and the next iteration needs the new `s`. That's a
**loop-carried dependency chain**: each add must wait 4 cycles for the previous add to finish. The loads of `a[i]`
are independent and can run ahead, but the adds can't overlap. So the loop runs at one add per 4 cycles, regardless
of the two FP units sitting mostly idle. At the test machine's ~2.7 GHz, 4 cycles is 1.48 ns, which is exactly the
1.49 ns measured.

`sum4` has **four independent chains**. While `s0`'s add is in flight, `s1`, `s2` and `s3` can start theirs. Four
chains of latency 4 fill the pipeline: up to one add per cycle completes, limited here by the loop's other work. The
measured 0.37 ns per element is about one cycle.

```text
sum1: s += a0 ....|s += a1 ....|s += a2 ....|s += a3 ....|   one add every 4 cycles
sum4: s0 += a0 ...|s0 += a4 ...|
       s1 += a1 ...|s1 += a5 ...|
        s2 += a2 ...|s2 += a6 ...|
         s3 += a3 ...|s3 += a7 ...|                          one add every cycle
```

The general principle, which you'll use again and again:

> **A loop's speed is bounded by the longer of: its longest loop-carried dependency chain (latency bound), and the
> total work divided by the machine's throughput for each resource (throughput bound).**

To get the full throughput of two FP units with 4-cycle latency, you need 2 × 4 = 8 independent chains in flight.
On the test machine, 8 scalar accumulators measured no faster than 4 (0.46 ns), because at that point the loop is
limited by loads, loop overhead and front-end bandwidth rather than adds. SIMD (Chapter 9) processes 4 doubles per
instruction and moves the bound again.

> [!WARNING]
> Why doesn't the compiler split the chain itself? Because floating-point addition isn't associative:
> `(a + b) + c` can differ from `a + (b + c)` in the last bits. Reordering would change the result, so the compiler
> may only do it under `-ffast-math` or `-fassociative-math`. For integer sums it's allowed and compilers do it.
> Chapter 14.1 explains the precision consequences.

### Exercises

:::exercise Latency or throughput bound?
Estimate cycles per iteration on Skylake for each loop body, and say what bounds it.
(a) `x = x * 1.0001;` (b) `y[i] = x[i] * 1.0001;` (c) `h = h * 31 + key[i];` (integer) (d) `q = q / d;` (`double`)
:::solution
(a) A chain through `x` with a 4-cycle multiply: **4 cycles**, latency bound. (b) Iterations are independent: one
load, one multiply, one store per element. The single store port limits it to about **1 cycle** (less once
vectorized): throughput bound. (c) A chain through `h`: `imul` (3) then `add` (1) = **4 cycles** per element,
latency bound. This is why fast hash functions process several independent lanes. (d) A chain through a 13–14
cycle divide: about **14 cycles**, latency bound. If `d` is a loop-invariant, multiply by a precomputed `1.0 / d`
instead (4 cycles, and usually acceptable precision).
:::

---

## 3. Out-of-order execution

A modern core doesn't execute instructions in program order. It looks ahead through a window of upcoming
instructions (the **reorder buffer**, 224 entries on Skylake) and executes each one as soon as its inputs are ready
and a unit is free. Results are then **retired** in program order, so the program behaves as if it ran sequentially.

This hides a lot of latency automatically:

```cpp
double a = x[i] * k;     // these two chains are independent:
double b = y[i] + c;     // the core overlaps them with no help from you
```

It's also why **independent cache misses overlap**. If a loop issues loads to ten unrelated addresses, the core can
have all ten misses outstanding at once (Skylake supports about 10–12 outstanding L1 misses per core). That's
**memory-level parallelism**, and it's why reading random addresses from an array costs far less per read than
walking a linked list, where each address depends on the previous load (Chapter 5.2).

What out-of-order execution *can't* do:

- **Break true dependency chains.** If each instruction needs the previous one's result, no amount of reordering
  helps. `sum1` is the canonical example; pointer chasing is the memory version.
- **Look infinitely far ahead.** A cache miss to DRAM takes ~300 cycles; at 4 instructions per cycle, the reorder
  buffer fills in about 60 cycles, and then the core stalls until the miss returns.
- **See past mispredicted branches.** All the work done after a mispredict is discarded.

> [!PERF]
> When you optimize a hot loop, first ask: **what is the critical dependency chain?** If there is one (an
> accumulator, a pointer chase, a recurrence like an EMA), its latency sets the speed, and the fix is to shorten it
> or split it into independent chains. If there isn't, count resources (loads, stores, multiplies) and compare them
> with the port limits.

---

## 4. Applying it: a recurrence in finance

Many financial calculations are recurrences: an exponential moving average, a GARCH variance update, compounding.

```cpp
// EMA over a price series: e = alpha * p + (1 - alpha) * e
double ema = prices[0];
for (std::size_t i = 1; i < n; ++i)
    ema = alpha * prices[i] + (1 - alpha) * ema;
```

The chain is `ema → multiply → add → ema`. Written with an FMA (`std::fma(beta, ema, alpha * p)`), it's one FMA per
iteration on the chain, about 4 cycles. The `alpha * prices[i]` part isn't on the chain and is free.

You can't split this into independent accumulators the way you can a sum, because each value genuinely depends on
the previous one. But you have other options:

- **Compute many series at once.** An EMA for 500 instruments is 500 independent chains. Interleave them (or use
  SIMD across instruments) and the latency is hidden completely. This is why "structure of arrays" layouts are so
  effective (Chapter 7.2).
- **Algebraic restructuring.** A recurrence `e[i] = b·e[i−1] + x[i]` can be unrolled to
  `e[i] = b²·e[i−2] + b·x[i−1] + x[i]`, halving the chain length at the cost of extra (independent) work. This
  changes rounding slightly.

### Exercises

:::exercise Many EMAs
You update EMAs for 8 instruments on each tick, one after another: `for (k = 0; k < 8; ++k) ema[k] = a * px[k] + b *
ema[k];`. Is this latency bound? What if you update one instrument's EMA over a history of 8 ticks instead?
:::solution
The eight updates in one tick are independent of each other (different `k`), so the core overlaps them: about 8
FMAs at a throughput of 2 per cycle, roughly 4 cycles in total plus overhead, not 8 × 4 = 32. Updating one
instrument over 8 historical ticks is a single chain of 8 dependent FMAs: about 32 cycles. Same arithmetic, 8× the
time.
:::

---

## Summary

- A pipelined, superscalar core can start several instructions per cycle, but each instruction takes several cycles
  to produce its result.
- **Latency** is how long until a result is ready; **throughput** is how many can start per cycle. A 4-cycle FP add
  with throughput 2 needs 8 independent operations in flight to keep both units busy.
- Loop-carried dependency chains make a loop latency bound. Breaking them into independent chains (multiple
  accumulators, many series at once) can give large speedups.
- Out-of-order execution overlaps independent work, including independent cache misses, but can't break true
  dependencies or see past a mispredicted branch.
- Integer and FP division are an order of magnitude slower than multiplication; hoist and invert divisors.

## Key terms

**Pipeline** · **superscalar** · **execution port** · **latency** · **throughput** · **dependency chain** ·
**loop-carried dependency** · **out-of-order execution** · **reorder buffer** · **retirement** ·
**memory-level parallelism**
