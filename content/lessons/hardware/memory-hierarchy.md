+++
title = "The Memory Hierarchy"
minutes = 40
practice_tags = ["cache", "memory", "measuring"]
+++

## Why this chapter matters

A risk service keeps a lookup table from instrument ID to position. When the firm traded 2,000 instruments, the
table was 200 KB and each lookup took about 3 ns. After expanding to 300,000 instruments, the table is 30 MB, and
each lookup takes about 100 ns, even though the code and the algorithm didn't change at all. The table simply
stopped fitting in the CPU's caches.

The single most important performance fact about modern computers is that **memory is slow and caches are small**.
How long a load takes depends far more on *where the data is* than on anything the instruction does. Chapter 5.3
covers cache lines and access patterns in detail; this chapter builds the big picture: what each level of the
hierarchy is, how to measure it, and how latency, bandwidth and parallelism interact.

By the end of this chapter you should be able to:

- describe each level of the memory hierarchy, with rough sizes, latencies and bandwidths;
- measure the latency of each level with a pointer-chasing benchmark, and read the resulting curve;
- explain the difference between latency-bound and bandwidth-bound code;
- use working-set size to predict when a data structure will become slow.

---

## 1. The levels

On the test machine (Intel Core i5-6500T, 4 cores, Skylake):

| Level | Size | Shared by | Measured load latency |
|---|---|---|---|
| Registers | 16 general-purpose + 16 vector (architectural) | one core | 0 |
| L1 data cache | 32 KB | one core | ~1.7 ns (4–5 cycles) |
| L2 cache | 256 KB | one core | ~3.4 ns (~12 cycles) |
| L3 cache | 6 MB | all 4 cores | ~10–16 ns |
| DRAM | 16+ GB | all cores (and sockets, Chapter 5.6) | ~100 ns, more with TLB misses |

Newer server CPUs have larger L2s (1–2 MB) and L3s (30–100+ MB shared), and slightly higher latencies at each level,
but the shape is the same everywhere: each level is several times larger and several times slower than the one
above it.

Some details that matter for performance:

- **Instructions have their own L1.** The L1 instruction cache (32 KB) holds code. Large hot paths with lots of
  inlined code can miss in it, which is a real cost in big trading applications.
- **L3 is shared.** Another process or thread on the same socket that streams through memory evicts your data from
  L3. This "noisy neighbour" effect is why latency-critical processes are often given a socket, or at least cores
  and cache partitions, to themselves (Chapter 10.4).
- **Caches hold copies.** Writes go to L1 and are written back to lower levels later (**write-back** caching).
  Keeping copies coherent between cores costs time when cores share written data (Chapter 10.3).

---

## 2. Measuring latency: pointer chasing

To measure the latency of a level, you need loads that **can't overlap**: each address must depend on the previous
load's result, so out-of-order execution (Chapter 5.1) can't start the next load early. A random cyclic permutation
does it:

```cpp
// next[i] holds the index to visit after i; all n indices form one random cycle
std::size_t chase(const std::vector<std::size_t>& next, int steps) {
    std::size_t p = 0;
    for (int i = 0; i < steps; ++i) p = next[p];   // each load's address comes from the previous load
    return p;
}
```

The randomness defeats the hardware prefetcher (Chapter 5.3). Varying the array size from KB to GB and dividing
the time by the number of steps gives latency as a function of working set. On the test machine:

| Working set | ns per load | Where the data lives |
|---|---|---|
| 16 KB | 1.65 | L1 |
| 128 KB | 3.40 | L2 |
| 512 KB | 10.1 | L3 |
| 4 MB | 15.5 | L3 (plus some L2 TLB misses) |
| 64 MB | 101 | DRAM |
| 512 MB | 118 | DRAM, plus page-table walks (Chapter 5.5) |

```svg "Latency per dependent load against working-set size on the test machine (log scale on the x axis). Each step in the curve is a cache level running out."
<svg viewBox="0 0 700 260" xmlns="http://www.w3.org/2000/svg" role="img"><line class="grid" x1="60" y1="220" x2="680" y2="220"/><line class="grid" x1="60" y1="220" x2="60" y2="20"/><polyline class="line" fill="none" points="80,217 200,214 280,203 360,194 480,49 600,20"/><circle class="f1" cx="80" cy="217" r="4"/><circle class="f1" cx="200" cy="214" r="4"/><circle class="f1" cx="280" cy="203" r="4"/><circle class="f1" cx="360" cy="194" r="4"/><circle class="f4" cx="480" cy="49" r="4"/><circle class="f4" cx="600" cy="20" r="4"/><text class="mono" x="70" y="240">16K</text><text class="mono" x="185" y="240">128K</text><text class="mono" x="265" y="240">512K</text><text class="mono" x="350" y="240">4M</text><text class="mono" x="465" y="240">64M</text><text class="mono" x="580" y="240">512M</text><text class="mono" x="88" y="208">1.7 ns</text><text class="mono" x="205" y="205">3.4</text><text class="mono" x="285" y="194">10</text><text class="mono" x="365" y="184">16 ns</text><text class="mono" x="490" y="54">101 ns</text><text class="mono" x="610" y="25">118</text><text x="10" y="30">ns</text></svg>
```

The curve is a staircase: flat while the working set fits in a level, then a jump when it spills to the next. The
jump from L3 to DRAM is by far the largest: **about 60× from L1 to DRAM**.

> [!TIP]
> Run this benchmark on your own machines, especially the production servers. Knowing the exact sizes and latencies
> of your hardware turns vague "cache-friendliness" into concrete numbers: "the book for this instrument is 40 KB,
> so it fits in L2 but not L1."

### Exercises

:::exercise Predict the lookup cost
A table of 300,000 entries, each 64 bytes, is accessed at random once per message. Using the latencies above, what
does each lookup cost? What if you shrink each entry to 16 bytes by moving cold fields elsewhere?
:::solution
300,000 × 64 B = 19.2 MB: larger than the 6 MB L3, so random lookups mostly go to DRAM, about **100 ns** each.
At 16 B per entry the table is 4.8 MB, which fits in L3 (if nothing else is competing for it): about **15 ns**.
Shrinking the hot data is often worth more than any code optimization. Chapter 7 is about doing that.
:::

---

## 3. Latency, bandwidth and memory-level parallelism

Latency isn't the whole story. When loads are **independent**, the core can have many cache misses outstanding at
once (memory-level parallelism, Chapter 5.1). Compare these two loops over the same 256 MB array:

```cpp
// Dependent: each address comes from the previous load
for (int i = 0; i < n; ++i) p = a[p];

// Independent: addresses are known in advance
for (auto i : random_indices) s += a[i];
```

On the test machine, the dependent chase costs **97 ns per load**, while the independent random reads cost **13 ns
per load**: about 7.5× faster, because roughly seven or eight misses are in flight at any moment. Both are random
accesses to DRAM; the only difference is whether the CPU can know the next address before the current load
finishes.

**Bandwidth** is the other limit: how many bytes per second can flow. Sequential access lets the prefetcher stream
whole cache lines ahead of use, and then the limit is bandwidth, not latency. A single core on the test machine
streams about **13 GB/s** from DRAM; all cores together get somewhat more, limited by the memory channels. Inside
the caches, bandwidth is much higher: two 32-byte loads per cycle from L1, about 170 GB/s per core at 2.7 GHz.

| Access pattern | Bound by | Cost per 8-byte element from DRAM (test machine) |
|---|---|---|
| Sequential scan | bandwidth | ~0.6 ns |
| Random, independent addresses | latency ÷ parallelism | ~13 ns |
| Random, dependent addresses (pointer chasing) | full latency | ~100 ns |

That's a **160× range** for the same amount of data, depending only on the access pattern.

> [!PERF]
> This table is the strongest argument in the book for arrays over linked structures. A linked list, a tree, or a
> hash table with chained nodes is a dependent chase: each step costs full latency. An array scan is
> bandwidth-bound. A batch of independent lookups (say, looking up the positions for all 50 orders in a message
> before processing any of them) lets the CPU overlap the misses.

---

## 4. Working set: the number that predicts performance

The **working set** of a piece of code is the amount of memory it touches repeatedly over a short period. Whether
that fits in L1, L2, L3 or none of them determines its speed far more than instruction counts.

For a trading system, estimate it per component:

- A strategy's state for one instrument: positions, a few indicators, recent prices. Often well under 1 KB, so
  L1-resident if the strategy runs alone on a core.
- An order book for a liquid instrument: a few thousand orders at 32–64 bytes each, plus levels: 100–500 KB.
  Fits in L2 or L3.
- A book for every instrument on an exchange: hundreds of MB. Most of it is cold most of the time; only active
  instruments stay cached.
- Code: the instruction working set of the hot path. Tens of KB for a lean handler; more if every message type
  inlines lots of logic.

When you add a feature, ask what it adds to the hot working set. A 2 KB lookup table touched on every message can
evict half of L1. A logging buffer written on every message pollutes the cache unless it's small and reused.

### Exercises

:::exercise Two tables
Two lookup tables are each accessed at random once per message: a 24 KB table and a 200 KB table. On the test
machine, roughly what does each lookup cost if the process runs alone on its core? What if a busy neighbour process
on the same socket constantly streams through gigabytes of data?
:::solution
Alone: the 24 KB table can stay in L1 (with some competition from other data), around 1.5–2 ns; the 200 KB table
fits in L2, around 3.5 ns. With the noisy neighbour: L1 and L2 are private to the core, so they're unaffected
directly, but the neighbour evicts lines from the shared L3, so anything that spills out of L2 (including the
200 KB table under pressure from your other data) now goes to DRAM, ~100 ns, instead of ~15 ns. This is why
low-latency deployments isolate cores and sometimes partition L3 (Intel CAT).
:::

---

## Summary

- Memory is a hierarchy: registers, L1 (~32 KB, ~1.5 ns), L2 (256 KB–2 MB, ~3–5 ns), shared L3 (MBs, ~10–20 ns), DRAM
  (GBs, ~100 ns). L1 to DRAM is roughly 60×.
- Measure latency with a random pointer chase; the latency-vs-size curve is a staircase.
- Independent misses overlap (memory-level parallelism); dependent ones don't. Random independent reads were 7.5×
  faster than a pointer chase on the test machine.
- Sequential scans are bandwidth-bound and very cheap per element.
- Estimate the working set of hot code; it predicts performance better than instruction counts.

## Key terms

**Memory hierarchy** · **L1 / L2 / L3** · **instruction cache** · **write-back** · **pointer chasing** ·
**latency** · **bandwidth** · **memory-level parallelism** · **working set** · **noisy neighbour**
