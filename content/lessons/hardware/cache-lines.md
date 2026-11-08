+++
title = "Cache Lines, Locality and Prefetching"
minutes = 45
practice_tags = ["cache", "memory", "data-layout"]
+++

## Why this chapter matters

Here are two functions that compute exactly the same thing, the sum of a 4096 × 4096 matrix of `int`s stored in
row-major order:

```cpp
#include <vector>
#include <cstdint>

constexpr int N = 4096;

int64_t sum_rows(const std::vector<int>& m) {         // walk along each row
    int64_t s = 0;
    for (int r = 0; r < N; ++r)
        for (int c = 0; c < N; ++c)
            s += m[r * N + c];
    return s;
}

int64_t sum_cols(const std::vector<int>& m) {         // walk down each column
    int64_t s = 0;
    for (int c = 0; c < N; ++c)
        for (int r = 0; r < N; ++r)
            s += m[r * N + c];
    return s;
}
```

Same number of additions, same data, same result. On the machine this book was tested on (an Intel Core
i5-6500T), `sum_rows` takes **6.0 ms** and `sum_cols` takes **37.6 ms**, more than 6 times slower. On machines
with less cache the gap is often 10–20×. Nothing in the source code, and nothing in a Big-O
analysis, predicts that. The explanation is entirely in how the CPU fetches memory.

This is the most important chapter in the performance half of the book. Most real-world speedups, including order
books, feature pipelines, risk aggregation and backtesters, come from **arranging data so the CPU reads it the way
caches like**, not from clever arithmetic.

By the end of this chapter you should be able to:

- quote rough latencies for each level of the memory hierarchy;
- explain what a cache line is and calculate how many lines an access pattern touches;
- distinguish spatial and temporal locality and restructure loops to exploit both;
- explain what the hardware prefetcher can and can't predict, and why linked structures are slow;
- apply these ideas to the layout of trading data structures.

---

## 1. The memory wall

A modern core can execute several instructions per cycle at 3–5 GHz, so roughly one instruction every 0.1 ns. Main
memory (DRAM) takes about **80–100 ns** to answer a request. If every load went to DRAM, the core would spend more
than 99% of its time waiting. To hide this, CPUs have a hierarchy of small, fast **caches**:

| Level | Typical size (per core unless noted) | Latency | ≈ Cycles at 4 GHz |
|---|---|---|---|
| Registers | ~200 physical | 0 | 0 |
| L1 data cache | 32–48 KB | ~1 ns | 4–5 |
| L2 cache | 1–2 MB | ~3–4 ns | 12–16 |
| L3 cache | 2–4 MB per core, shared (30+ MB total) | ~10–20 ns | 40–80 |
| DRAM | GBs | ~80–100 ns | 300–400 |

These are ballpark figures that vary by microarchitecture, but the **ratios** hold everywhere: each level is
roughly 3–10× slower than the one above it, and DRAM is about 100× slower than L1.

```svg "Load latency drawn to a linear scale. One DRAM miss costs as much time as about 90 L1 hits."
<svg viewBox="0 0 700 220" xmlns="http://www.w3.org/2000/svg" role="img"><defs><marker id="arrowhead" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path class="head" d="M0,0 L10,5 L0,10 z"/></marker></defs><text x="10" y="40">L1 · 32–48 KB</text><rect class="f2" x="150" y="20" width="5.222222222222222" height="30" rx="5"/><text class="mono" x="165.22222222222223" y="40">~1 ns</text><text x="10" y="90">L2 · 1–2 MB</text><rect class="f3" x="150" y="70" width="20.88888888888889" height="30" rx="5"/><text class="mono" x="180.88888888888889" y="90">~4 ns</text><text x="10" y="140">L3 · shared</text><rect class="f1" x="150" y="120" width="78.33333333333333" height="30" rx="5"/><text class="mono" x="238.33333333333331" y="140">~15 ns</text><text x="10" y="190">DRAM · GBs</text><rect class="f4" x="150" y="170" width="470.0" height="30" rx="5"/><text class="mono" x="630.0" y="190">~90 ns</text></svg>
```


> [!NOTE]
> A useful rule of thumb: an L1 hit costs about as much as an addition. A cache miss to DRAM costs as much as
> **several hundred additions**. For most programs memory access is the bottleneck, not computation.

### Exercises

:::exercise Back-of-envelope
A loop does 10 million independent loads, each a DRAM miss, with no overlap between them. Roughly how long does
it take? What if they all hit L1?
:::solution
DRAM: 10⁷ × ~90 ns ≈ **0.9 s**. L1: 10⁷ × ~1 ns ≈ **10 ms**, and in practice less, because the CPU can issue two or
three loads per cycle. In reality out-of-order execution overlaps several independent misses at once (memory-level
parallelism), so a DRAM-bound loop is better than the worst case. But it's still one to two orders of magnitude
slower than a cache-resident one.
:::

---

## 2. Cache lines: memory moves in 64-byte blocks

Caches don't store individual bytes. They store **cache lines**: aligned 64-byte blocks on x86-64 and most ARM
server chips. (Apple's M-series uses 128 bytes.) When you load one `int`, the entire 64-byte line containing it is
brought into L1.

```layout "Reading m[0] (pink) brings in the whole aligned line. m[1]…m[15] (green) come along for free."
title: one cache line = 64 bytes = 16 ints
row: 64
[0]:4:4 [1]:4:2 [2]:4:2 [3]:4:2 [4]:4:2 [5]:4:2 [6]:4:2 [7]:4:2 [8]:4:2 [9]:4:2 [10]:4:2 [11]:4:2 [12]:4:2 [13]:4:2 [14]:4:2 [15]:4:2
```


This has two consequences:

1. **Neighbours are free.** After loading `m[0]`, the next 15 `int`s (`m[1]`…`m[15]`) are already in L1. Reading
   them costs about 1 ns each.
2. **Wasted bytes are expensive.** If you only use 4 bytes of every 64-byte line you fetch, you're using 1/16 of
   your memory bandwidth and cache capacity.

That explains our matrix:

- `sum_rows` walks memory sequentially. Each line fetched serves **16** consecutive additions.
- `sum_cols` jumps `N × 4 = 16 KB` between consecutive accesses. Every access touches a **different line**, and by
  the time the loop comes back for the neighbouring column, that line has long been evicted (4096 lines × 64 B
  = 256 KB per column pass, far more than L1). So every single access is a miss.

```svg "Row-major storage: each row is contiguous in memory (rows shaded alternately). Only the left traversal follows the memory order."
<svg viewBox="0 0 680 262" xmlns="http://www.w3.org/2000/svg" role="img"><defs><marker id="arrowhead" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path class="head" d="M0,0 L10,5 L0,10 z"/></marker></defs><rect class="f2" x="70" y="40" width="26" height="26" rx="3"/><rect class="f2" x="98" y="40" width="26" height="26" rx="3"/><rect class="f2" x="126" y="40" width="26" height="26" rx="3"/><rect class="f2" x="154" y="40" width="26" height="26" rx="3"/><rect class="f2" x="182" y="40" width="26" height="26" rx="3"/><rect class="f2" x="210" y="40" width="26" height="26" rx="3"/><rect class="f3" x="70" y="68" width="26" height="26" rx="3"/><rect class="f3" x="98" y="68" width="26" height="26" rx="3"/><rect class="f3" x="126" y="68" width="26" height="26" rx="3"/><rect class="f3" x="154" y="68" width="26" height="26" rx="3"/><rect class="f3" x="182" y="68" width="26" height="26" rx="3"/><rect class="f3" x="210" y="68" width="26" height="26" rx="3"/><rect class="f2" x="70" y="96" width="26" height="26" rx="3"/><rect class="f2" x="98" y="96" width="26" height="26" rx="3"/><rect class="f2" x="126" y="96" width="26" height="26" rx="3"/><rect class="f2" x="154" y="96" width="26" height="26" rx="3"/><rect class="f2" x="182" y="96" width="26" height="26" rx="3"/><rect class="f2" x="210" y="96" width="26" height="26" rx="3"/><rect class="f3" x="70" y="124" width="26" height="26" rx="3"/><rect class="f3" x="98" y="124" width="26" height="26" rx="3"/><rect class="f3" x="126" y="124" width="26" height="26" rx="3"/><rect class="f3" x="154" y="124" width="26" height="26" rx="3"/><rect class="f3" x="182" y="124" width="26" height="26" rx="3"/><rect class="f3" x="210" y="124" width="26" height="26" rx="3"/><rect class="f2" x="70" y="152" width="26" height="26" rx="3"/><rect class="f2" x="98" y="152" width="26" height="26" rx="3"/><rect class="f2" x="126" y="152" width="26" height="26" rx="3"/><rect class="f2" x="154" y="152" width="26" height="26" rx="3"/><rect class="f2" x="182" y="152" width="26" height="26" rx="3"/><rect class="f2" x="210" y="152" width="26" height="26" rx="3"/><rect class="f3" x="70" y="180" width="26" height="26" rx="3"/><rect class="f3" x="98" y="180" width="26" height="26" rx="3"/><rect class="f3" x="126" y="180" width="26" height="26" rx="3"/><rect class="f3" x="154" y="180" width="26" height="26" rx="3"/><rect class="f3" x="182" y="180" width="26" height="26" rx="3"/><rect class="f3" x="210" y="180" width="26" height="26" rx="3"/><path class="arrow-good" d="M76,53 L230,53 L76,81 L230,81 L76,109 L230,109 L76,137 L230,137 L76,165 L230,165 L76,193 L230,193" stroke-linejoin="round" opacity=".85"/><rect class="f4" x="420" y="40" width="26" height="26" rx="3"/><rect class="box" x="448" y="40" width="26" height="26" rx="3"/><rect class="box" x="476" y="40" width="26" height="26" rx="3"/><rect class="box" x="504" y="40" width="26" height="26" rx="3"/><rect class="box" x="532" y="40" width="26" height="26" rx="3"/><rect class="box" x="560" y="40" width="26" height="26" rx="3"/><rect class="f4" x="420" y="68" width="26" height="26" rx="3"/><rect class="box" x="448" y="68" width="26" height="26" rx="3"/><rect class="box" x="476" y="68" width="26" height="26" rx="3"/><rect class="box" x="504" y="68" width="26" height="26" rx="3"/><rect class="box" x="532" y="68" width="26" height="26" rx="3"/><rect class="box" x="560" y="68" width="26" height="26" rx="3"/><rect class="f4" x="420" y="96" width="26" height="26" rx="3"/><rect class="box" x="448" y="96" width="26" height="26" rx="3"/><rect class="box" x="476" y="96" width="26" height="26" rx="3"/><rect class="box" x="504" y="96" width="26" height="26" rx="3"/><rect class="box" x="532" y="96" width="26" height="26" rx="3"/><rect class="box" x="560" y="96" width="26" height="26" rx="3"/><rect class="f4" x="420" y="124" width="26" height="26" rx="3"/><rect class="box" x="448" y="124" width="26" height="26" rx="3"/><rect class="box" x="476" y="124" width="26" height="26" rx="3"/><rect class="box" x="504" y="124" width="26" height="26" rx="3"/><rect class="box" x="532" y="124" width="26" height="26" rx="3"/><rect class="box" x="560" y="124" width="26" height="26" rx="3"/><rect class="f4" x="420" y="152" width="26" height="26" rx="3"/><rect class="box" x="448" y="152" width="26" height="26" rx="3"/><rect class="box" x="476" y="152" width="26" height="26" rx="3"/><rect class="box" x="504" y="152" width="26" height="26" rx="3"/><rect class="box" x="532" y="152" width="26" height="26" rx="3"/><rect class="box" x="560" y="152" width="26" height="26" rx="3"/><rect class="f4" x="420" y="180" width="26" height="26" rx="3"/><rect class="box" x="448" y="180" width="26" height="26" rx="3"/><rect class="box" x="476" y="180" width="26" height="26" rx="3"/><rect class="box" x="504" y="180" width="26" height="26" rx="3"/><rect class="box" x="532" y="180" width="26" height="26" rx="3"/><rect class="box" x="560" y="180" width="26" height="26" rx="3"/><path class="arrow-hot" d="M433,46 L433,200 L461,46 L461,200 L489,46 L489,200 L517,46 L517,200 L545,46 L545,200 L573,46 L573,200" stroke-linejoin="round" opacity=".85"/><text class="title" x="154" y="24" text-anchor="middle">sum_rows</text><text class="title" x="504" y="24" text-anchor="middle">sum_cols</text><text class="muted" x="154" y="232" text-anchor="middle">walks memory in order:</text><text class="muted" x="154" y="250" text-anchor="middle">1 miss per 16 ints</text><text class="muted" x="504" y="232" text-anchor="middle">jumps a whole row (16 KB) each step:</text><text class="muted" x="504" y="250" text-anchor="middle">a miss on every access</text></svg>
```


You can check the line size on Linux:

```bash
getconf LEVEL1_DCACHE_LINESIZE      # usually 64
lscpu | grep -i cache
```

In C++17 and later, `std::hardware_destructive_interference_size` gives the compiler's idea of this value. In
practice most low-latency codebases just hard-code 64.

### Exercises

:::exercise Lines touched
How many distinct cache lines does each loop touch, assuming `a` is a 64-byte-aligned array of 1024 `double`s?
(a) `for (i = 0; i < 1024; ++i) s += a[i];` (b) `for (i = 0; i < 1024; i += 8) s += a[i];`
(c) `for (i = 0; i < 1024; i += 16) s += a[i];`
:::solution
A line holds 64 / 8 = 8 doubles, so the array spans 128 lines. (a) **128** lines, each used 8 times.
(b) Stride 8 = one access per line, so still **128** lines, each used once: 1/8 of the work for the same memory
traffic, which is why it won't be anywhere near 8× faster. (c) Stride 16 skips every other line: **64** lines.
The lesson: *runtime tracks lines touched, not elements accessed.*
:::

:::exercise Struct scans
`struct Order { int64_t id; int64_t price; int32_t qty; char pad[44]; };` is exactly 64 bytes. A loop sums
`qty` over 1 million orders. How many bytes come in from memory, and how many does the loop actually use? How
could you restructure the data?
:::solution
One line per order, so 1M × 64 B = **64 MB** fetched to use 1M × 4 B = **4 MB**. That's 6% utilization. Store the
hot field separately (a `std::vector<int32_t> qty`), which is the *struct-of-arrays* layout covered in
Chapter 7.2. Then the scan reads 4 MB, about 16× less traffic.
:::

---

## 3. Spatial and temporal locality

Caches pay off when programs show **locality**:

- **Spatial locality**: after touching an address, you soon touch *nearby* addresses. You get it from contiguous
  storage and sequential traversal, like `std::vector` or arrays walked in order.
- **Temporal locality**: after touching an address, you touch *the same* address again soon. You get it from
  keeping your **working set** (the data a loop actively uses) small enough to stay in cache.

### Loop order and layout

The fix for `sum_cols` is to make the traversal order match the memory layout: in C++ the *last* index should
vary fastest. When you need both orders, as with a matrix transpose or a matrix multiply, use **blocking** (also
called tiling): process the data in tiles small enough to stay in L1 or L2, so each line fetched gets reused many
times before it's evicted.

```cpp
// Transpose an N x N matrix in B x B tiles so both src and dst stay cache-resident.
void transpose_blocked(const float* src, float* dst, int n) {
    constexpr int B = 32;                        // 32 x 32 floats = 4 KB per tile
    for (int ii = 0; ii < n; ii += B)
        for (int jj = 0; jj < n; jj += B)
            for (int i = ii; i < ii + B && i < n; ++i)
                for (int j = jj; j < jj + B && j < n; ++j)
                    dst[j * n + i] = src[i * n + j];
}
```

### Working set size

A classic experiment: repeatedly sum an array of size *S* and plot the time per element against *S*. The curve
has **steps** at the L1, L2 and L3 sizes. While the array fits in a level, each access costs that level's latency
(or bandwidth). Past the last level, you're limited by DRAM.

> [!QUANT]
> This is why market-making systems obsess over the *size* of their hot state. If the book for every instrument
> you trade, the strategy state and the risk limits all fit in L2, the hot path almost never misses. If they
> spill to DRAM, every tick costs hundreds of nanoseconds of stalls.

### Exercises

:::exercise Fix the loop
Rewrite `for (int j = 0; j < cols; ++j) for (int i = 0; i < rows; ++i) out[i][j] = a[i][j] * 2;` (for 2-D
arrays stored row-major) to be cache friendly. Why is it safe to swap the loops here?
:::solution
Swap them so `j`, the last index, is innermost:

```cpp
for (int i = 0; i < rows; ++i)
    for (int j = 0; j < cols; ++j)
        out[i][j] = a[i][j] * 2;
```

It's safe because every iteration is independent: no iteration reads a value another one writes. If an iteration
depended on an earlier one, such as `out[i][j] = out[i-1][j] + ...`, you'd have to check that the new order still
respects the dependencies.
:::

:::exercise Tile size
Why is B = 32 a reasonable tile size for transposing `float`s, given a 32 KB L1? What happens if you choose
B = 512?
:::solution
One 32 × 32 tile of floats is 4 KB. The source tile and destination tile together are 8 KB, comfortably inside a
32 KB L1 alongside everything else. With B = 512, a tile is 1 MB, which doesn't fit in L1 and only just fits in
L2, so the destination lines get evicted before they're reused and you lose most of the benefit. Tiles of 16–64
are the usual sweet spot. Measure on your machine.
:::

---

## 4. The hardware prefetcher, and why pointers hurt

CPUs include **hardware prefetchers** that watch your access pattern and fetch lines *before* you ask for them.
They are very good at:

- sequential access, forwards or backwards;
- constant strides (every *k*-th line), including several independent streams at once.

They **can't** predict addresses that depend on data you haven't loaded yet. That's exactly what pointer-chasing
structures do:

```cpp
struct Node { int64_t value; Node* next; };

int64_t sum_list(const Node* n) {
    int64_t s = 0;
    for (; n; n = n->next)     // can't know the next address until this load completes
        s += n->value;
    return s;
}
```

Each iteration must wait for the previous load to finish before it even knows which address to load next. If the
nodes are scattered across the heap, as they become after a while of inserts and deletes, every step is a full
DRAM round trip of about 90 ns. Summing a `std::vector<int64_t>` of the same size runs at memory bandwidth, about
about 0.6 ns per element on our test machine. **That's a 160× difference for the same Big-O** (Section 5 has the measurements).

This is why experienced performance engineers default to contiguous containers:

```svg "Walking a vector is a straight line through memory that the prefetcher can predict. Walking a list jumps to an address it only learns after the previous load completes."
<svg viewBox="0 0 680 275" xmlns="http://www.w3.org/2000/svg" role="img"><defs><marker id="arrowhead" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path class="head" d="M0,0 L10,5 L0,10 z"/></marker></defs><text class="title" x="20" y="24">std::vector: one contiguous block</text><rect class="f2" x="20" y="36" width="74" height="34" rx="8"/><text class="mono" x="57.0" y="57.0" text-anchor="middle">v[0]</text><rect class="f2" x="98" y="36" width="74" height="34" rx="8"/><text class="mono" x="135.0" y="57.0" text-anchor="middle">v[1]</text><rect class="f2" x="176" y="36" width="74" height="34" rx="8"/><text class="mono" x="213.0" y="57.0" text-anchor="middle">v[2]</text><rect class="f2" x="254" y="36" width="74" height="34" rx="8"/><text class="mono" x="291.0" y="57.0" text-anchor="middle">v[3]</text><rect class="f2" x="332" y="36" width="74" height="34" rx="8"/><text class="mono" x="369.0" y="57.0" text-anchor="middle">v[4]</text><rect class="f2" x="410" y="36" width="74" height="34" rx="8"/><text class="mono" x="447.0" y="57.0" text-anchor="middle">v[5]</text><rect class="f2" x="488" y="36" width="74" height="34" rx="8"/><text class="mono" x="525.0" y="57.0" text-anchor="middle">v[6]</text><rect class="f2" x="566" y="36" width="74" height="34" rx="8"/><text class="mono" x="603.0" y="57.0" text-anchor="middle">v[7]</text><path class="arrow-good" d="M30,82 L640,82" marker-end="url(#arrowhead)"/><text class="title" x="20" y="122">std::list: nodes scattered across the heap</text><rect class="grid" x="10" y="134" width="660" height="130" rx="10" stroke-dasharray="5 4"/><rect class="f4" x="40" y="150" width="66" height="28" rx="8"/><text class="mono" x="73.0" y="168.0" text-anchor="middle">n0</text><rect class="f4" x="470" y="215" width="66" height="28" rx="8"/><text class="mono" x="503.0" y="233.0" text-anchor="middle">n1</text><rect class="f4" x="250" y="150" width="66" height="28" rx="8"/><text class="mono" x="283.0" y="168.0" text-anchor="middle">n2</text><rect class="f4" x="560" y="152" width="66" height="28" rx="8"/><text class="mono" x="593.0" y="170.0" text-anchor="middle">n3</text><rect class="f4" x="130" y="218" width="66" height="28" rx="8"/><text class="mono" x="163.0" y="236.0" text-anchor="middle">n4</text><rect class="f4" x="380" y="170" width="66" height="28" rx="8"/><text class="mono" x="413.0" y="188.0" text-anchor="middle">n5</text><rect class="f4" x="600" y="222" width="66" height="28" rx="8"/><text class="mono" x="633.0" y="240.0" text-anchor="middle">n6</text><rect class="f4" x="300" y="222" width="66" height="28" rx="8"/><text class="mono" x="333.0" y="240.0" text-anchor="middle">n7</text><path class="arrow-hot" d="M106,164 Q288.0,132 470,229" marker-end="url(#arrowhead)" opacity=".8"/><path class="arrow-hot" d="M536,229 Q393.0,132 250,164" marker-end="url(#arrowhead)" opacity=".8"/><path class="arrow-hot" d="M316,164 Q438.0,132 560,166" marker-end="url(#arrowhead)" opacity=".8"/><path class="arrow-hot" d="M626,166 Q378.0,134 130,232" marker-end="url(#arrowhead)" opacity=".8"/><path class="arrow-hot" d="M196,232 Q288.0,152 380,184" marker-end="url(#arrowhead)" opacity=".8"/><path class="arrow-hot" d="M446,184 Q523.0,152 600,236" marker-end="url(#arrowhead)" opacity=".8"/><path class="arrow-hot" d="M666,236 Q483.0,204 300,236" marker-end="url(#arrowhead)" opacity=".8"/></svg>
```


| Container | Layout | Traversal |
|---|---|---|
| `std::vector`, `std::array`, `std::deque` (mostly) | contiguous | prefetcher-friendly |
| `std::list`, `std::forward_list` | one node per element | pointer chasing |
| `std::map`, `std::set` | red-black tree nodes | pointer chasing, O(log n) misses per lookup |
| `std::unordered_map` | bucket array + linked nodes | at least one miss per lookup, often two |

> [!PERF]
> Big-O tells you how work grows. It says nothing about the constant factor, which here differs by 100×. For
> *n* in the thousands, a linear scan of a sorted `std::vector` often beats `std::map` lookups, and inserting into
> the middle of a vector (a `memmove` of contiguous bytes) often beats `std::list`, despite the "worse" complexity.

### Software prefetch

When you *do* know an address ahead of time, for example processing a batch of order IDs that index into a large
table, you can issue `__builtin_prefetch(ptr)` a few iterations early. It's a niche tool and easy to misuse.
Chapter 8.5 covers it with measurements.

### Exercises

:::exercise Vector vs list
You need a container of 1,000 resting orders that you iterate in full on every tick, with occasional inserts and
removals in the middle. Which is likely faster, `std::vector<Order>` or `std::list<Order>`, and why?
:::solution
Almost certainly `std::vector`. A full iteration runs every tick and is sequential and prefetched with a vector,
versus up to 1,000 dependent cache misses with a list. Middle inserts and removals in a vector move at most a few
tens of KB with `memmove`, which is fast because it's contiguous, and they're rarer than full scans anyway. If
the order of orders doesn't matter, removing by swapping the element with the last one and popping makes removal
O(1) as well.
:::

:::exercise Why is the tree slow?
A `std::map<int64_t, Level>` holds 10,000 price levels. Roughly how many cache misses might one lookup take, and
what alternative layout could you use for a book whose prices lie in a known band?
:::solution
A red-black tree with 10⁴ nodes has depth around log₂(10⁴) ≈ 14 (up to about 2× that in the worst case). The top
few levels stay hot in cache, but the lower levels are scattered across the heap, so expect **several** misses per
lookup, each up to about 90 ns. If prices fall in a known band, say ±5,000 ticks around a reference price, use a
**flat array indexed by tick offset**: `levels[price_ticks - base_ticks]`. A lookup is then one address
computation and usually one cache hit. The [Limit Order Book project](/projects/order-book.html) builds exactly this.
:::

---

## 5. Measured: the whole chapter in one benchmark

Every claim in this chapter is cheap to check. The program
`examples/cache_bench.cpp` in the project folder
runs four experiments. Build it with optimizations and run it:

```bash
g++ -std=c++20 -O2 examples/cache_bench.cpp -o cache_bench && ./cache_bench
```

The measuring harness is simple: run the work a few times, keep the best time, and write the result to a
`volatile` so the optimizer can't delete the loop:

```cpp
template <class F>
double time_ms(F&& f, int reps = 5) {          // best of `reps` runs, in milliseconds
    double best = 1e300;
    for (int r = 0; r < reps; ++r) {
        auto t0 = std::chrono::steady_clock::now();
        f();
        auto t1 = std::chrono::steady_clock::now();
        best = std::min(best, std::chrono::duration<double, std::milli>(t1 - t0).count());
    }
    return best;
}

volatile int64_t sink;                          // the result goes here so the work isn't optimized away
double rows = time_ms([&] { sink = sum_rows(m); });
```

Here are the results on an Intel Core i5-6500T (32 KB L1d and 256 KB L2 per core, 6 MB shared L3), compiled with
GCC at `-O2`:

### Experiment 1: traversal order

| Loop | Time |
|---|---|
| `sum_rows` (sequential) | 5.98 ms |
| `sum_cols` (16 KB stride) | 37.64 ms (**6.3× slower**) |

### Experiment 2: stride through a 64 MB array of `int`

| Stride (ints) | Accesses | Time | ns per access |
|---|---|---|---|
| 1 | 16.8 M | 9.82 ms | 0.59 |
| 2 | 8.4 M | 6.47 ms | 0.77 |
| 4 | 4.2 M | 4.56 ms | 1.09 |
| 8 | 2.1 M | 3.91 ms | 1.86 |
| 16 (one per line) | 1.0 M | 3.98 ms | 3.80 |
| 32 (every 2nd line) | 0.5 M | 3.90 ms | 7.44 |
| 64 (every 4th line) | 0.26 M | 2.65 ms | 10.11 |

Read this table carefully, because it's the chapter in miniature:

- Going from stride 1 to stride 16 does **16× less work but is only 2.5× faster**. The time is set by the
  1 million cache lines fetched, not by the 16.8 million additions.
- Stride 16 and stride 32 take the **same time**, even though stride 32 touches only half the lines. Intel's
  *adjacent-line prefetcher* fetches lines in 128-byte pairs, so skipping every other line saves nothing.
- Only at stride 64, every fourth line, does memory traffic actually drop.

### Experiment 3: traversing 2²⁰ `int64_t`s

| Container | ns per element |
|---|---|
| `std::vector` | 0.60 |
| `std::list`, nodes allocated in order | 3.20 (5× slower) |
| `std::list`, nodes linked in shuffled order | 96.2 (**160× slower**) |

The shuffled list is the realistic case for a long-running process: each step is a dependent DRAM miss of about
100 ns, close to the raw memory latency. A freshly built list *looks* acceptable only because the allocator
happened to hand out consecutive addresses.

### Experiment 4: summing one field over 4 million orders

| Layout | Time |
|---|---|
| `std::vector<Order>` with 64-byte `Order` structs | 16.16 ms |
| `std::vector<int32_t>` holding just the `qty` field | 1.80 ms (**9× faster**) |

Same data, same sum. The dense array reads 16 MB instead of 256 MB.

> [!TIP]
> Numbers like these vary between machines, so run the benchmark yourself. Change `N`, the strides, or the struct
> size and predict the result *before* you run it. Being able to predict performance is the skill this part of
> the book is building.

### Exercises

:::exercise Predict, then measure
Using the stride table above, predict the time for stride 128 (every 8th line). Then add it to the benchmark and
check.
:::solution
From stride 64 to 128 the number of lines fetched halves again, and the prefetcher can no longer help much, so you
should see roughly half the time. Measured on the test machine: **1.52 ms** (11.6 ns per access). The ns per access keeps rising towards the
true DRAM latency, because each access is now an isolated miss. If your result differs a lot, the likely culprits
are the L2 *streamer* prefetcher (which can detect large strides within a 4 KB page) or TLB effects: stride 128
ints is 512 bytes, so 8 accesses per page.
:::

:::exercise Explain the 5×
Why is the *fresh* list 5× slower than the vector even though its nodes are laid out in order?
:::solution
Three reasons. (1) Each node is about 32 bytes (16 bytes of `prev`/`next` pointers, 8 bytes of value, plus
allocator overhead), so only 2 elements fit per cache line instead of 8: 4× more memory traffic. (2) Each step
still loads `next` before it knows the next address, so the loop is a serial dependency chain that can't be
vectorized or unrolled. (3) The vector loop is vectorized, adding several elements per instruction.
:::

---

## 6. Applying it: cache-conscious trading data

The principles reduce to a few rules you can apply to any hot data structure:

1. **Contiguous over node-based.** Vectors, arrays and flat hash tables beat lists, trees and chained hash maps.
2. **Pack hot data densely.** Shrink structs (Chapter 1.3), split rarely used fields into a separate cold struct,
   and use the smallest types that are safe.
3. **Access in memory order.** Make loops walk memory sequentially. Batch work so each line is reused while it's
   hot.
4. **Keep the working set small.** Know how many bytes the hot path touches per event, and compare that with
   your L1 and L2 sizes.
5. **Measure.** Use `perf stat -e cache-misses,cache-references,L1-dcache-load-misses` to confirm that a change
   really reduced misses (Chapter 6.2).

Here's a hot/cold split for orders:

```cpp
#include <cstdint>
#include <string>
#include <vector>

struct OrderHot {          // touched on every book update: 16 bytes, 4 per cache line
    int64_t price_ticks;
    int32_t qty;
    int32_t next_index;    // intrusive FIFO link within the price level, as an index rather than a pointer
};

struct OrderCold {         // touched on fills and reporting only
    int64_t     client_order_id;
    int64_t     entry_time_ns;
    std::string account;
    std::string strategy_tag;
};

struct OrderStore {
    std::vector<OrderHot>  hot;    // same index in both vectors refers to the same order
    std::vector<OrderCold> cold;
};
```

Notice `next_index` is a 32-bit **index into a vector**, not a pointer. It's half the size, it stays valid when
the vector is reallocated, and because the orders live in one contiguous block, the "linked list" mostly stays
within a few hot cache lines.

```layout "Hot/cold split: the book only touches OrderHot, and four of them fit in a cache line. The cold data (strings, IDs, timestamps) lives in a separate array."
title: OrderHot: 16 bytes
row: 16
price_ticks:8:1 qty:4:2 next_index:4:3

title: OrderCold: 80 bytes (with libstdc++ strings), touched rarely
row: 16
client_order_id:8:5 entry_time_ns:8:6 account_(std::string):32:4 strategy_tag_(std::string):32:4
```


### Exercises

:::exercise Design review
A colleague proposes `std::unordered_map<std::string, Instrument>` to look up instruments by symbol on every
market data message. Name two cache-related problems and propose a better design.
:::solution
(1) `std::unordered_map` stores each element in its own heap node, so a lookup is a bucket-array access plus at
least one dependent miss to the node. (2) `std::string` keys must be hashed every time, and long symbols live in
yet another heap allocation, which adds another potential miss. Better: map symbols to dense integer IDs once, at
subscription time (the exchange's instrument ID is often already a small integer), then index a
`std::vector<Instrument>` by ID on the hot path. If you must look up by string at runtime, use a flat
open-addressing table with short fixed-size keys stored inline.
:::

:::exercise Measure it
Write a benchmark that sums a `std::vector<int64_t>` and a `std::list<int64_t>` of 10⁶ elements and reports
ns per element. Then shuffle the list's node allocation order (for example, insert at random positions) and
measure again. What do you observe?
:::solution
On the test machine (Section 5): vector 0.6 ns/element (bandwidth bound and vectorized). A freshly built list
whose nodes happen to be allocated consecutively ≈ 1–3 ns/element: the allocator placed the nodes in order, so
the prefetcher still mostly works. After shuffling, the list rises to **about 96 ns/element** because every
step is a dependent DRAM miss. The gap between the "fresh" and "shuffled" list is exactly why benchmarks of
node-based containers on freshly allocated data look misleadingly good. Long-running systems fragment their heaps.
:::

---

## Summary

- DRAM is ~100× slower than L1. **Memory access patterns dominate performance.**
- Memory moves in **64-byte cache lines**. Runtime tracks lines touched, not elements accessed.
- Exploit **spatial locality** (contiguous data, sequential access) and **temporal locality** (small working sets,
  blocking).
- The **prefetcher** handles sequential and strided access, but not pointer chasing. Prefer contiguous containers.
- For trading data: dense hot structs, hot/cold splitting, indices instead of pointers, flat arrays indexed by
  price, and measurement with `perf`.

## Key terms

**Cache line** · **cache hit / miss** · **spatial locality** · **temporal locality** · **working set** ·
**blocking / tiling** · **hardware prefetcher** · **pointer chasing** · **hot/cold splitting**
