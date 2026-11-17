+++
title = "Array of Structs vs Struct of Arrays"
minutes = 40
practice_tags = ["data-layout", "cache", "memory"]
+++

## Why this chapter matters

You store top-of-book quotes for many instruments as a vector of structs, `std::vector<Quote>`, with `bid`, `ask`,
`bid_sz` and `ask_sz` as `double`s. A signal needs the spread of every instrument. Another component needs one
instrument's full quote when a message arrives. Should the four fields live together in one struct per instrument,
or in four separate arrays?

Measured on the test machine, the answer depends entirely on the access pattern:

| Workload | Array of structs (AoS) | Struct of arrays (SoA) | Winner |
|---|---|---|---|
| Spread of 4,096 quotes, in L1/L2, AVX2 | 1.09 ns/quote | 0.38 ns/quote | SoA, 2.9× |
| Spread of 8 M quotes, from DRAM | 2.48 ns/quote | 1.44 ns/quote | SoA, 1.7× |
| Microprice (all four fields) of 8 M quotes | 2.85 ns/quote | 2.26 ns/quote | SoA, 1.3× |
| Random lookup of one quote's four fields, 8 M quotes | 19 ns/lookup | 139 ns/lookup | **AoS, 7×** |

Layout is not a style choice: it can make one workload several times faster and another several times slower. This
chapter explains the two layouts, why each wins where it does, the hybrid that gets the best of both, and how to
write SoA code without making it unreadable.

By the end of this chapter you should be able to:

- describe AoS, SoA and AoSoA layouts and their memory access patterns;
- predict which layout wins for a given workload, using bytes touched and number of cache lines per access;
- explain why SoA vectorizes well and AoS often doesn't;
- implement an SoA container with a clean interface.

---

## 1. The two layouts

**Array of structs (AoS)**: each element's fields are together; elements follow one another.

```cpp
struct Quote { double bid, ask, bid_sz, ask_sz; };   // 32 bytes
std::vector<Quote> quotes;                           // bid ask bsz asz | bid ask bsz asz | ...
```

**Struct of arrays (SoA)**: each field has its own array; the *i*-th element is spread across them.

```cpp
struct Quotes {
    std::vector<double> bid, ask, bid_sz, ask_sz;    // bid bid bid ... | ask ask ask ... | ...
};
```

```layout "AoS stores each quote's fields together. SoA stores each field contiguously. Computing every spread from AoS reads all 64 bytes of each line but uses only 32 of them; from SoA, every byte read is used."
title: AoS: two quotes per 64-byte cache line
row: 64
bid0:8:1 ask0:8:2 bsz0:8:3 asz0:8:4 bid1:8:1 ask1:8:2 bsz1:8:3 asz1:8:4

title: SoA: one cache line of the bid array (8 bids)
row: 64
bid0:8:1 bid1:8:1 bid2:8:1 bid3:8:1 bid4:8:1 bid5:8:1 bid6:8:1 bid7:8:1
```

---

## 2. Why each wins where it does

Count **bytes moved** and **cache lines touched** per element (Chapters 5.2 and 5.3):

**Scanning a subset of fields.** The spread needs `bid` and `ask`: 16 of the 32 bytes. AoS drags all 32 bytes
through the memory hierarchy; SoA moves only the two arrays. Including the 8-byte output, that's 40 bytes per
quote versus 24. From DRAM, where the loop is bandwidth bound, the measured speedup (1.7×) is almost exactly
40 ÷ 24.

**Vectorization.** In cache, bandwidth isn't the limit, and the bigger effect is SIMD (Chapter 9). With SoA, eight
consecutive `bid`s are contiguous, so one AVX2 load fetches four of them and one `vsubpd` computes four spreads. With
AoS, the four bids are 32 bytes apart; the compiler must either gather them with shuffles or give up vectorizing.
That's the 2.9× in the first row.

**Using all fields.** When the computation needs every field, both layouts move the same bytes, and the difference
shrinks to whatever vectorization buys (1.3× here, limited by the division).

**Random access to whole records.** Looking up one instrument's full quote is one cache line with AoS (all 32 bytes
are together), but **four** cache lines with SoA, one in each array, at four unrelated addresses. With 8 M quotes
in DRAM, that's four misses instead of one, plus four TLB entries instead of one. Measured: 139 ns versus 19 ns.

The rule of thumb:

> **SoA wins when operations touch a few fields of many elements. AoS wins when operations touch many fields of a
> few elements.**

Trading systems have both: a feed handler updating one instrument's quote per message (AoS-like access), and a risk
scan over every instrument (SoA-like access). Which one is on the latency-critical path should drive the layout.

### Exercises

:::exercise Choose the layout
Choose AoS or SoA for each: (a) an order book's resting orders, where each operation finds one order by ID and reads
or updates its price, quantity and links; (b) daily close prices for 5,000 instruments over 10 years, used to compute
each instrument's volatility; (c) per-instrument state in a strategy that, on every market-data message, reads and
updates 6 fields of that one instrument; (d) Greeks for 100,000 option positions, summed into portfolio totals every
second.
:::solution
(a) AoS: each operation touches most fields of one order. (b) SoA by instrument (one contiguous time series per
instrument), since the computation scans one instrument's prices over time. (This is a "row vs column" question:
you want each series contiguous.) (c) AoS: one record, many fields, random instrument order. (d) SoA: each total sums
one field across all positions, an ideal vectorized scan.
:::

---

## 3. AoSoA: the hybrid

**Array of structs of arrays** (AoSoA, also called "tiled" or "blocked" SoA) groups elements into small blocks,
storing each block in SoA form:

```cpp
struct QuoteBlock {                  // 8 quotes, SoA within the block: 4 × 64 = 256 bytes
    alignas(64) double bid[8];
    double ask[8];
    double bid_sz[8];
    double ask_sz[8];
};
std::vector<QuoteBlock> blocks;      // quote i lives at blocks[i / 8], lane i % 8
```

- **Scans** still read contiguous runs of each field: each field's 8 values fill exactly one cache line and two AVX2
  registers, so loops vectorize as well as plain SoA.
- **Random access** to one quote touches 4 cache lines, but all within one 256-byte block: adjacent lines that share
  a page (one TLB entry) and that the adjacent-line prefetcher often brings in together. Much better than SoA's four
  scattered addresses.
- Only one allocation, and one index to manage.

The block width is usually chosen to match the SIMD width or the cache line (8 `double`s = 64 bytes).

---

## 4. Writing SoA code that stays readable

The main objection to SoA is ergonomic: `quotes.bid[i]`, `quotes.ask[i]` everywhere, and the fields' vectors can get
out of sync. Encapsulate the storage behind an interface that keeps them consistent:

```cpp
class QuoteTable {
public:
    std::size_t add(double bid, double ask, double bsz, double asz) {
        bid_.push_back(bid); ask_.push_back(ask); bsz_.push_back(bsz); asz_.push_back(asz);
        return bid_.size() - 1;
    }
    void update(std::size_t i, double bid, double ask) { bid_[i] = bid; ask_[i] = ask; }

    std::size_t size() const { return bid_.size(); }
    std::span<const double> bids() const { return bid_; }      // whole-column access for vectorized scans
    std::span<const double> asks() const { return ask_; }

    struct QuoteRef { double& bid; double& ask; double& bid_sz; double& ask_sz; };
    QuoteRef operator[](std::size_t i) { return {bid_[i], ask_[i], bsz_[i], asz_[i]}; }   // row-style access

private:
    std::vector<double> bid_, ask_, bsz_, asz_;   // invariant: all the same size
};

// A column scan reads like math, and vectorizes:
void spreads(const QuoteTable& t, std::span<double> out) {
    auto b = t.bids(), a = t.asks();
    for (std::size_t i = 0; i < t.size(); ++i) out[i] = a[i] - b[i];
}
```

Two refinements used in production code:

- Allocate all columns in **one** buffer (with each column aligned to 64 bytes) to reduce allocations and TLB entries.
- Generate the class from a field list with a macro or, in C++26, with reflection, so adding a field is one line.

> [!NOTE]
> When writing a loop over several SoA columns, the compiler may worry that the output array overlaps an input and
> refuse to vectorize, or add run-time overlap checks. Passing spans of `const` inputs helps; Chapter 8.2 explains
> aliasing and `__restrict` fully.

### Exercises

:::exercise Size the block
For AoSoA with `float` fields and AVX2 (256-bit registers), what block width would you pick, and how big is a block of
a 5-field struct?
:::solution
A 256-bit register holds 8 floats, and 16 floats fill a 64-byte cache line. Picking 16 per block gives each field
exactly one cache line per block (two AVX2 registers): 5 fields × 64 B = 320 bytes per block. Picking 8 gives 32-byte
field runs, so two fields share a cache line; that's fine for scans but means random access touches 3 lines instead
of 5. Either is reasonable; measure with your access mix.
:::

---

## Summary

- AoS keeps each element's fields together; SoA keeps each field's values together; AoSoA stores small SoA blocks.
- Count bytes moved and cache lines touched per element to predict the winner.
- SoA wins for scans over a few fields (less bandwidth, clean vectorization): 1.7–2.9× on the test machine.
- AoS wins for random access to whole records (one cache line instead of one per field): 7× on the test machine.
- AoSoA gets SoA's scan speed with much better random-access locality.
- Hide SoA storage behind an interface with column and row access.

## Key terms

**Array of structs (AoS)** · **struct of arrays (SoA)** · **AoSoA** · **column access** · **row access** ·
**bytes moved per element** · **gather** · **vectorization-friendly layout**
