+++
title = "Branch Hints, Prefetch and Other Hints"
minutes = 35
practice_tags = ["branchless", "cache"]
+++

## Why this chapter matters

It's tempting to sprinkle hints through hot code: `[[likely]]` on the common branch, `__builtin_prefetch` ahead of
every lookup, `[[assume]]` wherever you know something the compiler doesn't. Sometimes they help. Often they do
nothing. Occasionally they make things worse. Here are random lookups into a 256 MB table on the test machine, with
and without software prefetching a few elements ahead:

| Workload | No prefetch | Best prefetch distance | Worst prefetch distance |
|---|---|---|---|
| sum one field per lookup | 12.5 ns | 11.8 ns (distance 64) | 13.9 ns (distance 4) |
| lookup plus ~160 cycles of work per element | 53.6 ns | 56.0 ns (distance 8) | 72.7 ns (distance 1) |

In the first case the best prefetch distance bought 6%, and a poor one cost 11%. In the second, prefetching only made
things slower. The hardware was already overlapping these independent misses (Chapter 5.2), so the extra
instructions were pure overhead.

Hints are a way of giving the compiler or CPU information it can't work out itself. This chapter covers the main
ones, what each actually changes, and how to tell whether one is worth keeping. The short version: **hints are for
cases you've measured, not for decoration.**

By the end of this chapter you should be able to:

- explain what `[[likely]]`/`[[unlikely]]` and `__builtin_expect` change, and what they don't;
- use `__builtin_prefetch` correctly, choose a distance, and recognize when the hardware already does the job;
- use `[[assume]]` and `std::unreachable()` to state facts the optimizer can exploit, and understand the risk;
- know the cold/hot function attributes and when to use them.

---

## 1. Branch likelihood

```cpp
if (msg.len < kHeaderLen) [[unlikely]] {
    return reject(msg);
}
```

`[[likely]]` and `[[unlikely]]` (C++20) and the older `__builtin_expect(cond, expected)` tell the compiler which way a
branch usually goes. As Chapter 5.4 explained, they **don't** affect the CPU's branch predictor; modern x86 cores
ignore static hints and predict from history. What they change is the **compiler's** decisions:

- **Code layout**: the likely path becomes the straight-line fall-through, and the unlikely path is moved out of the
  way, often to the end of the function or to a cold section. The hot path gets denser in the instruction cache, and
  a taken jump is avoided on the common path.
- **Optimization effort**: code on the unlikely path may be optimized for size and won't receive inlining bonuses.
- **If-conversion decisions**: a branch marked strongly biased is less likely to be turned into a `cmov`, which is
  right, since predictable branches are cheaper than conditional moves.

When does it matter? When the compiler's static guess is wrong, and the path is hot enough for layout to show up.
Compilers already assume that branches leading to `throw`, `abort`, or functions marked `cold` or `noreturn` are
unlikely, and that loop back-edges are taken. So annotations add the most on validation and error checks that just
`return` an error code, which look like ordinary branches to the compiler.

> [!TIP]
> If you find yourself annotating dozens of branches, use PGO (Chapter 8.4) instead: it gets likelihoods for every
> branch from real data. Reserve manual hints for a handful of important cases, such as the "not enough data yet"
> check at the top of a parser, or risk rejections that should never be on the fast path.

### Hot and cold functions

At the function level:

```cpp
[[gnu::cold, gnu::noinline]] void log_and_reject(const Order& o, RejectReason r);
[[gnu::hot]] void on_market_data(const Msg& m);
```

`cold` tells the compiler that calls to the function are unlikely (so branches leading to it are laid out as cold),
optimizes the function for size, and places it in `.text.unlikely`, away from hot code. `hot` does the reverse.
Marking error-handling and logging functions `cold` is one of the most reliable ways to keep the hot path compact.

---

## 2. Software prefetch

```cpp
__builtin_prefetch(addr);              // read, high temporal locality: bring into all cache levels
__builtin_prefetch(addr, 1);           // prefetch for writing
__builtin_prefetch(addr, 0, 0);        // non-temporal: try not to pollute the caches
_mm_prefetch(reinterpret_cast<const char*>(addr), _MM_HINT_T0);   // the intrinsic equivalent
```

A prefetch asks the CPU to start loading a cache line now, without waiting for it, so that a later load finds it in
cache. It can't fault, even on an invalid address, and it doesn't block.

### When it doesn't help

The hardware already does a great deal of prefetching:

- The **hardware prefetchers** detect sequential and strided access patterns within a page and fetch ahead
  automatically (Chapter 5.3). Software prefetch for a sequential scan is pointless.
- **Out-of-order execution** issues independent loads early, overlapping their misses. A loop of random but
  *independent* lookups, as in the table above, already has ~7 misses in flight (Chapter 5.2), which is why software
  prefetch barely moved it.

Software prefetch also costs something: an extra instruction (often an extra load to compute the future address,
like `ids[i + d]` above), a slot in the limited set of outstanding-miss buffers, and cache space if the line is
evicted before use. A prefetch that's too close (distance 1 above) arrives too late to help and still costs; one
that's too far may be evicted before use.

### When it does help

Prefetch can pay off when the CPU can't discover the future addresses itself, and you can:

- **Dependent or irregular access where you know addresses early**: for example, a batch of hash-table lookups:
  compute all the bucket addresses for 16 keys first, prefetch them all, then probe. Each probe has branches and
  dependent loads that limit how far ahead out-of-order execution can see, but the bucket addresses were known up
  front.
- **Linked structures where you can reach ahead**: when processing the orders at a price level, prefetch the node
  two or three ahead if the list layout makes their addresses available (an index array, say).
- **Access patterns the hardware prefetcher doesn't follow**: large strides that cross pages, or alternating between
  many streams.
- **Before a burst of work on known data**: warming the cache lines of an instrument's state as soon as a packet's
  header names the instrument, while the rest of the packet is still being parsed.

The distance should cover the miss latency: roughly *miss latency ÷ time per iteration*. With ~100 ns misses and
~10 ns of work per iteration, that's about 10 iterations ahead. Then **measure**, because the right value depends on
the machine and the load.

> [!WARNING]
> Prefetches tuned on one CPU can hurt on another with different latencies, prefetchers or miss-buffer counts.
> Treat every software prefetch as a measured, documented, per-platform decision, and re-measure after hardware
> changes.

### Exercises

:::exercise Prefetch or not?
Would software prefetch likely help? (a) Summing a `std::vector<double>` of 100 million elements. (b) Looking up 1,000
independent order IDs in a 1 GB open-addressing table, one after another with no other work. (c) Walking a
`std::list` of 10,000 nodes allocated at random times through the day. (d) Processing a batch of 32 messages, each
naming an instrument whose 64-byte state is in a 50 MB table, with ~200 ns of parsing per message.
:::solution
(a) No: sequential access is handled by the hardware prefetcher. (b) Probably little: the lookups are independent, so
out-of-order execution overlaps the misses already (as measured in this chapter); huge pages (Chapter 5.5) would
likely help more. (c) Not directly: each node's address is only known after loading the previous node, so you can't
prefetch far ahead; the fix is a better data structure. (d) Yes, plausibly: parse all 32 headers first (cheap) and
prefetch each instrument's state, then do the full processing; by the time each message is handled, its state has
had hundreds of nanoseconds to arrive. Measure to confirm.
:::

---

## 3. Assumptions: `[[assume]]` and `std::unreachable`

Sometimes you know a fact that the compiler can't prove, and that would let it generate better code:

```cpp
void scale(double* x, std::size_t n) {
    [[assume(n % 8 == 0 && n > 0)]];                 // C++23; GCC/Clang also: __builtin_assume, if (!c) __builtin_unreachable()
    for (std::size_t i = 0; i < n; ++i) x[i] *= 2;   // no remainder loop, no zero-trip check
}

int decode_side(char c) {
    switch (c) {
        case 'B': return +1;
        case 'S': return -1;
    }
    std::unreachable();                              // C++23: input has been validated; no default path needed
}
```

Typical uses: telling the compiler that a size is a multiple of the vector width, that a value is within a range (so
a `switch` needs no bounds check), that pointers are aligned (`std::assume_aligned<64>(p)` in C++20), or that a
branch can't be taken.

The catch is severe: if an assumption is ever false, the behaviour is **undefined**, with all the consequences of
Chapter 8.3. The compiler won't check it; it builds code that's only correct when it holds. A safe pattern is to make
the assumption checked in debug builds and assumed in release:

```cpp
#ifdef NDEBUG
#define ASSUME(c) [[assume(c)]]
#else
#define ASSUME(c) assert(c)
#endif
```

> [!IMPORTANT]
> Only assume facts that are guaranteed by construction (validated input, a constant you control, a protocol
> invariant you've checked at the boundary), and expect the gain to be small: removing a remainder loop or a bounds
> check rarely matters unless the loop is tiny and very hot. Check the assembly to confirm the assumption did
> something.

---

## 4. A checklist for any hint

1. **Is the compiler or CPU actually getting this wrong?** Check the assembly (layout, `cmov` vs branch, remainder
   loops) and the counters (`perf stat` for branch and cache misses) first.
2. **Make the change and measure** on realistic data, with enough repetitions to see small differences (Chapter 6.1).
3. **Keep it only if it helps**, and leave a comment saying what was measured, on what hardware.
4. **Prefer structural fixes** where possible: better data layout beats prefetching, PGO beats hand-written
   likelihoods, branchless code (Chapter 9.3) beats hoping a branch predicts.

---

## Summary

- `[[likely]]`/`[[unlikely]]` change code layout and optimization effort, not the hardware predictor. They help when
  the compiler's static guess is wrong on a hot path. PGO does the same, everywhere, from data.
- `cold`/`noinline` on error and logging functions reliably keeps hot paths compact.
- Software prefetch helps only when the hardware can't find future addresses itself. Measured on the test machine:
  for independent random lookups it gained at most 6% and could cost 11–36% at bad distances.
- `[[assume]]`, `std::unreachable` and `std::assume_aligned` state facts the optimizer can use; if they're false, the
  behaviour is undefined.
- Every hint is a measured, documented decision.

## Key terms

**Branch likelihood** · **`[[likely]]` / `[[unlikely]]`** · **`__builtin_expect`** · **code layout** ·
**`cold` / `hot` attribute** · **software prefetch** · **prefetch distance** · **hardware prefetcher** ·
**`[[assume]]`** · **`std::unreachable`** · **`std::assume_aligned`**
