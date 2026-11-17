+++
title = "Alignment, Padding and Packing"
minutes = 35
practice_tags = ["alignment", "memory", "cache"]
+++

## Why this chapter matters

Chapter 1.3 introduced the basics: every type has an alignment, the compiler pads structs to satisfy it, and ordering
fields from largest to smallest removes most of the padding. This chapter goes further, because in performance work
the exact byte layout of hot structures matters in several more ways:

- An order struct of 72 bytes spans two cache lines, so every access to it costs two lines of bandwidth and cache
  space. Trimming it to 64 bytes and aligning it to a line boundary halves that.
- A binary market-data message arrives with an 8-byte price at offset 13. Reading it with a cast to `int64_t*` is
  undefined behaviour; reading it with `memcpy` is free.
- A struct marked "packed" to save space makes a hot loop slower, because every field access may straddle a cache
  line.

By the end of this chapter you should be able to:

- inspect a struct's exact layout with `offsetof`, `-Wpadded` and `pahole`;
- design hot structs that fit in, and are aligned to, cache lines;
- explain what misaligned and line-splitting loads cost, measured;
- use `alignas`, aligned allocation and packing correctly, and know when packing hurts.

---

## 1. Seeing the layout

Don't guess layouts; check them.

```cpp
struct Order {
    std::uint64_t id;
    std::int64_t  price;
    std::int32_t  qty;
    Side          side;          // enum class : uint8_t
    std::uint64_t timestamp;
};

static_assert(sizeof(Order) == 32);
static_assert(offsetof(Order, timestamp) == 24);   // 3 bytes of padding after side
```

Tools:

- **`static_assert` on `sizeof` and `offsetof`**: documents the layout and breaks the build if someone changes it
  unintentionally. Do this for every struct that's on a hot path, in shared memory, or on the wire.
- **`-Wpadded`**: GCC and Clang warn about every padding byte they insert. Too noisy to leave on globally, but useful
  on one header.
- **`pahole`** (from the `dwarves` package) prints every struct in a binary built with `-g`, with offsets, sizes,
  holes and cache-line boundaries:

```text
struct Order {
        uint64_t                   id;                   /*     0     8 */
        int64_t                    price;                /*     8     8 */
        int32_t                    qty;                  /*    16     4 */
        Side                       side;                 /*    20     1 */

        /* XXX 3 bytes hole, try to pack */

        uint64_t                   timestamp;            /*    24     8 */

        /* size: 32, cachelines: 1, members: 5 */
        /* sum members: 29, holes: 1, sum holes: 3 */
};
```

---

## 2. Designing for cache lines

A cache line is 64 bytes on x86 (Chapter 5.3). For a struct that's accessed as a unit (an order, a price level, an
instrument's state):

**Fit it in one line if you can.** A 72-byte struct is two lines; a 64-byte one is one. Shaving a few bytes across a
size boundary can halve the memory traffic of every access. Common savings: 32-bit quantities and indices instead of
64-bit where the range allows (Chapter 3.7's `smallest_uint_t`), 8-bit enums, timestamps relative to session start in
32 bits, booleans folded into flag bits, and moving cold fields out (Chapter 7.1).

**Align it to a line boundary.** A 64-byte struct starting at offset 32 straddles two lines anyway. `alignas(64)` on
the type forces every instance to start on a boundary:

```cpp
struct alignas(64) InstrumentState {      // exactly one cache line per instrument
    std::int64_t  bid, ask;
    std::int32_t  bid_qty, ask_qty;
    std::int64_t  position;
    std::int64_t  last_trade_px;
    std::uint64_t last_update_ts;
    std::uint32_t flags;
    // 12 bytes left for future fields
};
static_assert(sizeof(InstrumentState) == 64);
```

**Smaller sizes should divide 64.** A 16- or 32-byte struct in an array never straddles a line (if the array is
aligned). A 24-byte one straddles every third element, and a 48-byte one every other.

**Order fields by access, not just by size.** For structs bigger than a line, put the fields that are used together
in the same line, hottest first. The fields touched on every message should share the first line.

> [!WARNING]
> `alignas(64)` makes `sizeof` a multiple of 64, which can waste a lot of memory for small types. Use it for
> structures accessed as units in hot paths, and especially for data written by different threads (false sharing,
> Chapter 10.3), not as a reflex.

### Allocating aligned memory

Since C++17, `new` honours over-alignment: `new InstrumentState[n]` and `std::vector<InstrumentState>` return
64-byte-aligned storage automatically. For raw buffers, use `std::aligned_alloc(alignment, size)` (size must be a
multiple of the alignment) or `operator new(size, std::align_val_t{64})`.

### Exercises

:::exercise Shrink to one line
An order struct contains: `uint64_t id`, `int64_t price`, `int64_t qty`, `int64_t filled_qty`, `uint64_t timestamp`
(ns since epoch), `uint32_t trader_id`, `uint16_t venue`, `Side side` (1 byte), `OrderType type` (1 byte),
`char symbol[8]`, `uint32_t next`, `uint32_t prev`. What's its size, and how could it fit in 48 bytes?
:::solution
Sum of members: 8 × 5 + 4 + 2 + 1 + 1 + 8 + 4 + 4 = 64 bytes, with no padding if the fields are ordered
largest first: exactly one cache line already. To reach 48 bytes, find 16 bytes of savings:

- `qty` and `filled_qty` as `int32_t` (−8), if no order can exceed about 2 billion shares;
- the 8-byte symbol replaced by a 32-bit instrument index into a reference table (−4);
- the timestamp as a 32-bit offset from session start (−4). In microseconds, 32 bits only covers 71 minutes, which
  is too short for a trading day; in milliseconds it covers 49 days. So this works only if millisecond precision
  is enough for this struct. Otherwise keep 64 bits and find the savings elsewhere.

Whether each trade-off is acceptable is a domain decision. The point is that every byte has a cost in a struct
accessed millions of times, and a 48-byte struct in an aligned array still straddles a line every other element, so
a 32- or 64-byte target is often better than 48.
:::

---

## 3. Misaligned and split loads, measured

x86 allows **misaligned** loads and stores of ordinary integers and floating-point values (unlike some other
architectures). The question is what they cost. Measured on the test machine with a chain of dependent 8-byte
loads, all hitting L1:

| Load address | Latency |
|---|---|
| 8-byte aligned | 1.66 ns |
| misaligned by 1, within one cache line | 1.64 ns |
| spanning two cache lines (offset 60 of a line) | 3.61 ns |
| spanning two pages (offset 4092 of a page) | 3.92 ns |

So on modern x86, **misalignment within a cache line is free**. What costs is a **split load** across a line
boundary: the CPU must read two lines and combine them, roughly doubling latency. (Page splits were far worse on
older CPUs; Skylake handles them almost as fast as line splits.) In a throughput-bound loop reading consecutive
8-byte values at an odd offset, only one load in eight splits, and the measured effect disappeared in the noise
(0.43 ns per load either way).

Two places where alignment still matters a great deal:

- **Atomics**: a misaligned atomic operation that splits a cache line takes a bus lock, which is catastrophically
  slow and affects every core. `std::atomic<T>` is always correctly aligned unless you defeat it with packing or
  casts.
- **Aligned SIMD instructions** (`movaps`, `_mm256_load_pd`) fault on misaligned addresses. The unaligned versions
  (`movups`, `_mm256_loadu_pd`) cost the same when the data happens to be aligned, so compilers generally use them
  (Chapter 9.2).

### Misalignment in C++ is still undefined behaviour

The hardware tolerating misaligned loads doesn't make this legal:

```cpp
const std::byte* msg = ...;                                   // a received buffer
auto price = *reinterpret_cast<const std::int64_t*>(msg + 13); // UB: misaligned and violates strict aliasing
```

The compiler may assume pointers to `int64_t` are 8-byte aligned and generate instructions (like aligned SIMD loads
in a vectorized loop) that fault. UBSan reports it as `misaligned address`. The correct form is `memcpy`, which
compiles to a single unaligned `mov`:

```cpp
std::int64_t price;
std::memcpy(&price, msg + 13, sizeof price);                  // well defined, and exactly as fast
```

Chapter 13.2 builds a zero-copy binary decoder on this.

---

## 4. Packing: when and why not

`#pragma pack(1)` or `[[gnu::packed]]` removes all padding:

```cpp
struct [[gnu::packed]] WireAddOrder {     // mirrors an exchange message byte for byte
    char          type;                   // offset 0
    std::uint16_t locate;                 // offset 1  (misaligned)
    std::uint64_t timestamp;              // offset 3  (misaligned)
    std::uint64_t order_id;               // offset 11
    char          side;                   // offset 19
    std::uint32_t shares;                 // offset 20
    char          symbol[8];              // offset 24
    std::uint32_t price;                  // offset 32
};
static_assert(sizeof(WireAddOrder) == 36);
```

Packed structs are useful for describing **wire and file formats** exactly. But:

- Field accesses can be misaligned, and an array of packed structs puts fields across line boundaries.
- Taking the address of a packed member and using it as an ordinary `T*` is UB (the pointer may be misaligned); GCC
  warns with `-Waddress-of-packed-member`.
- Atomics in packed structs may be misaligned, with the bus-lock cost above.

The usual practice: use packed structs (or, better, explicit `memcpy` at known offsets) **only at the boundary**,
to decode a message into a normal, naturally aligned internal struct, and keep all hot processing on the internal
form.

### Bit-fields

Bit-fields pack small values into fewer bytes:

```cpp
struct Flags {
    std::uint8_t side      : 1;
    std::uint8_t is_ioc    : 1;
    std::uint8_t is_hidden : 1;
    std::uint8_t tif       : 3;   // time in force, 0–7
};
static_assert(sizeof(Flags) == 1);
```

Reading a bit-field costs a shift and a mask; writing costs a read-modify-write of the containing byte. That's
usually a good trade in a hot struct (smaller structs, fewer cache lines). Their layout is implementation-defined,
though, so never use bit-fields to describe wire formats; decode with explicit shifts and masks instead. And two
threads writing different bit-fields of the same byte race with each other.

---

## Summary

- Check layouts with `static_assert(sizeof / offsetof)`, `-Wpadded` and `pahole`.
- Make hot structs fit in a cache line, align them with `alignas(64)`, and prefer sizes that divide 64.
- On x86, misalignment within a cache line is free; loads that split a line cost about 2× (measured 3.6 ns vs
  1.7 ns). Split atomics are disastrous.
- Misaligned access through a typed pointer is still UB in C++. Use `memcpy`, which compiles to one load.
- Use packed structs only at wire/file boundaries; decode into naturally aligned internal structs.
- Bit-fields shrink structs cheaply, but their layout isn't portable.

## Key terms

**Alignment** · **padding** · **`offsetof`** · **`pahole`** · **`alignas`** · **over-aligned allocation** ·
**misaligned access** · **split load** · **packed struct** · **bit-field**
