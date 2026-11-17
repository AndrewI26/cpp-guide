+++
title = "Branch Prediction"
minutes = 40
practice_tags = ["branchless", "algorithms"]
+++

## Why this chapter matters

A loop sums the quantities of orders on one side of the book: `if (o.side == Side::Buy) total += o.qty;`. On the test
machine it runs at about **1.1 ns per order** when the orders happen to be grouped by side, and **4.8 ns per order**
when buys and sells are randomly interleaved, the same code on the same amount of data. The difference is entirely
the CPU's **branch predictor** guessing wrong about half the time.

In Chapter 5.1 we saw that a modern core works on dozens of instructions at once, far ahead of the one currently
completing. To do that, it has to know which instructions come next, *before* it has computed the conditions of the
branches in between. So it guesses. When it guesses right, branches are nearly free. When it guesses wrong, it
throws away everything it did on the wrong path. Market data is often unpredictable by nature, so understanding
this matters a lot in trading code.

By the end of this chapter you should be able to:

- explain how branch prediction works and what a misprediction costs;
- predict which branches will be well predicted and which won't;
- measure the effect with a controlled benchmark;
- remove unpredictable branches with conditional moves, arithmetic, and data reorganization;
- know when `[[likely]]`/`[[unlikely]]` help and when they don't.

---

## 1. Speculation and misprediction

When the front end of the core reaches a conditional branch, the condition usually isn't computed yet; it may depend
on a load that's still in flight. Rather than wait, the core **predicts** the direction and continues fetching and
executing along the predicted path: **speculative execution**. When the condition is finally known:

- **Correct prediction:** the speculative work is kept. The branch cost about as much as any other instruction.
- **Misprediction:** all instructions after the branch are discarded, the pipeline is refilled from the correct
  target, and the core has lost the work of roughly the pipeline depth. On Skylake that's about **15–20 cycles**,
  around 6–8 ns at the test machine's clock.

Measured on the test machine: a loop with a data-dependent branch costs 1.11 ns per element when the branch always
goes the same way, and 4.84 ns per element when it's random (taken 50% of the time). Half the iterations mispredict,
so each mispredict costs about (4.84 − 1.11) / 0.5 ≈ **7.5 ns**, or about 20 cycles.

| Fraction taken (random) | ns per element | Mispredict rate (roughly) |
|---|---|---|
| 0% | 1.11 | ~0% |
| 1% | 1.41 | ~1% |
| 5% | 1.68 | ~5% |
| 10% | 2.05 | ~10% |
| 25% | 3.26 | ~25% |
| 50% | 4.84 | ~50% |

A branch that's rarely taken is cheap even when the rare cases are random: the predictor guesses "not taken" and is
usually right. The expensive branches are the ones that are close to 50/50 *and* random.

---

## 2. What predictors can learn

Modern predictors (TAGE-style designs on recent Intel and AMD cores) use the **history** of recent branch outcomes,
both for this branch and globally across branches, to predict the next one. They're remarkably good at:

- **Biased branches**: almost always taken, or almost never. Error checks, `if (size == 0)`, loop back-edges.
- **Repeating patterns**: taken every 4th time, TTFTTF..., alternating. On the test machine, a branch taken exactly
  once every *k* iterations ran at 1.0–1.5 ns per element for every period up to 64; the predictor learned all of
  them.
- **Correlated branches**: a branch whose outcome is determined by an earlier one.
- **Loop exits** for loops with a short, fixed trip count.

What they can't learn is genuinely random data. And much of the data in trading is close to random at fine
granularity: whether the next message is a buy or a sell, whether a price ticked up or down, which of several
message types arrives next, whether an order crosses the spread.

> [!NOTE]
> Predictors have finite tables. A hot path with thousands of distinct branches can exceed them, and branches
> that run rarely (like the "send an order" path, which may run once in thousands of messages) get evicted
> and mispredict when they finally execute. Chapter 12.2 covers keeping them warm.

### Exercises

:::exercise Predictable or not?
For each branch, would you expect good or poor prediction?
(a) `if (msg.type == 'A')` in an ITCH feed where 'A' (add order) is about 45% of messages, randomly mixed;
(b) `if (qty <= 0) reject();`;
(c) `if (i % 4 == 0) flush();`;
(d) `if (price > ema)` for a price series that oscillates around its moving average;
(e) the loop condition in `for (int i = 0; i < 1000; ++i)`.
:::solution
(a) Poor: close to 50/50 and effectively random message to message. (b) Good: almost never taken in correct
operation. (c) Good: a period-4 pattern that the predictor learns. (d) Poor: crossing back and forth around a
moving average looks random tick to tick. (e) Good: taken 999 times, mispredicted once at exit.
:::

---

## 3. Removing unpredictable branches

When a branch is unpredictable and hot, the fix is to compute the result **without** a branch, so there's nothing
to mispredict. The CPU then always does both "sides" of the work, but that's usually a few cycles, versus 20 for a
mispredict.

### Conditional moves

x86 has `cmov`: copy a value if a flag is set. It's a data dependency, not a control dependency, so it can't
mispredict. Compilers generate it for simple ternaries and `if`s:

```cpp
long sum_buys(const Order* o, int n) {
    long total = 0;
    for (int i = 0; i < n; ++i)
        total += (o[i].side == Side::Buy) ? o[i].qty : 0;    // usually compiles to cmov (or SIMD)
    return total;
}
```

In fact, GCC at `-O2` converted the first version of the benchmark for this chapter into branch-free code on its own
(**if-conversion**), so the benchmark ran at the same speed for every data pattern. To measure a real branch, the
taken path had to contain something the compiler couldn't convert. Always check the assembly (Chapter 6.3) before
concluding how a branch is being handled.

The compiler won't always if-convert: when one side has side effects (a store, a function call), when it's
expensive, or when its heuristics guess the branch is predictable.

### Arithmetic and masks

You can write branch-free code explicitly with arithmetic on comparison results:

```cpp
total += o[i].qty & -static_cast<long>(o[i].side == Side::Buy);   // mask is all ones or all zeros
total += o[i].qty * (o[i].side == Side::Buy);                     // multiply by 0 or 1
```

On the test machine, the mask version ran at **0.56 ns per element for every input distribution**: 2× faster than the
best case of the branchy version (which still pays for the branch instruction and loop structure) and 8.6× faster
than its worst case. Chapter 9.3 covers branchless techniques in depth, including min/max, clamping, and branchless
binary search.

> [!WARNING]
> Branchless isn't automatically faster. It always does the work of both paths, and it turns a control dependency
> into a data dependency, which can lengthen a critical chain (Chapter 5.1). For a branch that's 99% predictable,
> the branchy version is often faster. Measure with realistic data distributions.

### Change the data, not the code

Sometimes the best fix is to make the branch predictable:

- **Sort or partition first.** If you'll process many items with a data-dependent branch, grouping them (all buys,
  then all sells) makes the branch trivially predictable. The classic demonstration: summing elements above a
  threshold is several times faster on sorted data. The sort only pays off if you'd process the data more than once,
  or if the data is naturally produced in groups.
- **Split by type up front.** Instead of one loop with a `switch` on each message's type, bucket messages by type
  and run a tight loop per type.
- **Table lookups.** Replace a chain of `if`s that maps a byte to a category with a 256-entry table (Chapter 3.3).

### Exercises

:::exercise Make it branchless
Write branch-free versions of: (a) `int clamp_qty(int q, int lo, int hi)`; (b) the sign of a price change:
`-1`, `0` or `+1`; (c) `best = (bid > best) ? bid : best;` for `int64_t`.
:::solution
(a) `return std::min(std::max(q, lo), hi);`: `std::min`/`max` on integers compile to `cmp` + `cmov`. (b)
`return (d > 0) - (d < 0);`: two comparisons producing 0 or 1, subtracted. (c) `best = std::max(best, bid);`, which
compiles to `cmov`. In all three cases, write the simple form and check the assembly: compilers handle these
reliably, and hand-written bit tricks can make code harder to read for no gain.
:::

---

## 4. Indirect branches and hints

### Indirect branches

A virtual call, a call through a function pointer, or a `switch` compiled to a jump table is an **indirect branch**:
the target address comes from data. The CPU predicts the *target* using the branch target buffer, keyed by the
branch's address and recent history. A virtual call that always goes to the same function predicts perfectly. A
`switch` on random message types, or a virtual call over a vector of mixed derived types, mispredicts often, with
the same ~20-cycle penalty.

That's another reason `std::variant` dispatch (Chapter 12.5) and type-sorted processing can beat virtual calls on
mixed data: not the call itself, but the misprediction.

### `[[likely]]` and `[[unlikely]]`

C++20's attributes tell the compiler which way a branch usually goes:

```cpp
if (msg.size < kHeaderLen) [[unlikely]] {
    return reject(msg);
}
```

They **don't** control the hardware predictor; modern x86 CPUs ignore static hints. What they change is **code
layout**: the compiler puts the likely path in the straight-line fall-through position and moves the unlikely path
out of the way, which keeps the hot path compact in the instruction cache and avoids a taken jump on the common
path. That's worth a little, mostly for error handling in the middle of hot code. Chapter 8.5 measures it, along
with profile-guided optimization, which does the same thing automatically and better.

---

## Summary

- The CPU predicts every branch and executes speculatively. A misprediction discards in-flight work: about 15–20
  cycles, ~7.5 ns measured on the test machine.
- Predictors learn biased branches, repeating patterns and correlations. They can't learn random 50/50 data, which is
  common in market data.
- Unpredictable hot branches can be replaced by conditional moves or arithmetic. The compiler often does this
  itself; check the assembly.
- Branchless code always does both paths' work; for highly predictable branches it can be slower.
- Sorting, partitioning or bucketing data can make branches predictable.
- Indirect branches mispredict on mixed targets. `[[likely]]`/`[[unlikely]]` affect code layout, not the predictor.

## Key terms

**Branch predictor** · **speculative execution** · **misprediction penalty** · **biased branch** ·
**branch history** · **conditional move (`cmov`)** · **if-conversion** · **branchless code** · **indirect branch** ·
**branch target buffer** · **`[[likely]]` / `[[unlikely]]`**
