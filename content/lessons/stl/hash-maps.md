+++
title = "Hash Maps and Their Discontents"
minutes = 45
practice_tags = ["data-structures", "memory", "cache"]
+++

## Why this chapter matters

Every order book needs a map from order ID to order: each cancel and execute message names an order by ID, and you
must find it fast. The obvious choice is `std::unordered_map<std::uint64_t, Order>`. It's O(1) on average. Yet in
a profile of a feed handler, that one map is often the single largest consumer of cycles: a heap allocation on
every add, a free on every delete, and two or three cache misses on every lookup.

An **open-addressing** hash map that stores entries directly in one flat array, with a good integer hash, is
routinely **3–5× faster** on the same workload. Knowing why requires understanding how `std::unordered_map` is built,
and why the standard effectively forces that design on it.

By the end of this chapter you should be able to:

- describe the memory layout of `std::unordered_map` and count the cache misses per lookup;
- explain open addressing, linear probing, load factor and tombstones;
- choose and write good hash functions for integer and string keys;
- decide when to use the standard map, a third-party flat map, or a custom table.

---

## 1. How `std::unordered_map` is built

The standard requires that references and pointers to elements stay valid across rehashing, and it exposes a
"bucket interface" (`bucket_count()`, `begin(n)` for bucket `n`). Together these practically mandate **separate
chaining with individually allocated nodes**:

```layout "std::unordered_map: a bucket array of pointers, each leading to a chain of separately allocated nodes. libstdc++ actually links all nodes into one singly linked list and points each bucket at the node before its first element, but the cost model is the same. (For key types with slow hashes, libstdc++ also caches the hash in each node.)"
title: bucket array (one heap block)
row: 64
b0:8:2 b1:8:2 b2:8:2 b3:8:2 b4:8:2 b5:8:2 b6:8:2 b7:8:2

title: one node: std::pair<const uint64_t, Order> plus links (one heap block per element)
row: 64
next:8:3 key:8:1 Order_(32_bytes):32:4 malloc_header:8:6
```

A lookup does:

1. Hash the key and reduce it modulo the bucket count (libstdc++ uses a prime number of buckets, so this is an
   integer **division**, ~20–40 cycles).
2. Load the bucket pointer: likely a cache miss for a large table.
3. Follow it to the first node: another likely miss, since the node was allocated independently.
4. Compare keys; if it's not a match, follow `next`: another miss.

So a typical successful lookup in a large table costs **2–3 cache misses**, and every insert costs a `malloc`
while every erase costs a `free`.

> [!NOTE]
> `std::hash<std::uint64_t>` in libstdc++ and libc++ is the **identity function**. That's fine with prime bucket
> counts, but a disaster in a power-of-two table, where only the low bits pick the bucket and keys like
> `0x1000, 0x2000, 0x3000` (common for IDs with structure in them) all collide.

### Exercises

:::exercise Count the misses
Estimate the time for one million random lookups in a `std::unordered_map<std::uint64_t, Order>` with 10 million
entries, assuming every node and bucket access misses to DRAM at ~80 ns, and an average chain length of 1.
:::solution
Each lookup: bucket array load (miss) + first node load (miss) ≈ 2 × 80 ns = 160 ns, plus the hash division and a
bit more for occasional longer chains. So roughly **160–200 ms** for a million lookups. Out-of-order execution can
overlap misses from *independent* lookups, so a tight loop of lookups might achieve 2–4× better throughput, but the
latency of any single lookup stays around 160 ns.
:::

---

## 2. Open addressing

An **open-addressing** table stores entries directly in one array of slots. Each key hashes to a home slot; if
that's taken, the key goes in the next free slot according to a **probe sequence**. The simplest and, on modern
hardware, usually the fastest is **linear probing**: try `h`, `h+1`, `h+2`, ... wrapping around.

```cpp
template <class V>
class FlatMap {
    struct Slot { std::uint64_t key; V value; bool used = false; };
    std::vector<Slot> slots_;           // capacity is a power of two
    std::size_t mask_, size_ = 0;
public:
    explicit FlatMap(std::size_t capacity_pow2) : slots_(capacity_pow2), mask_(capacity_pow2 - 1) {}

    V* find(std::uint64_t key) {
        for (std::size_t i = hash(key) & mask_;; i = (i + 1) & mask_) {
            Slot& s = slots_[i];
            if (!s.used) return nullptr;            // hit an empty slot: key isn't present
            if (s.key == key) return &s.value;
        }
    }
    // insert: probe the same way until an empty slot or the key; erase: see tombstones below
};
```

A lookup is: one multiply-and-shift hash, one AND with the mask, and usually **one cache miss** to the home slot.
Subsequent probes are in the same or the next cache line, which the prefetcher handles. No allocation on insert
(until the table grows), no pointer chasing.

### Load factor

The **load factor** is `size / capacity`. With linear probing, the expected probe length grows quickly as the table
fills: around 1.5 probes for a successful lookup at load 0.5, about 2.5 at 0.75, and much worse beyond 0.9, because
occupied slots clump into long runs (**primary clustering**). Typical designs grow the table (doubling and
reinserting everything) at a load factor of 0.5–0.875.

Because trading systems usually know their maximum size (orders live per session, there's a cap on outstanding
orders), the best approach is often to **size the table once at start-up** for the peak with a comfortable load
factor, so it never rehashes during trading.

### Deletion and tombstones

You can't simply mark a slot empty on erase: that would break the probe chain for keys inserted after it, which
probed past this slot. Two approaches:

- **Tombstones**: mark the slot "deleted". Lookups skip over it; inserts may reuse it. Tombstones accumulate and
  lengthen probes, so the table must be rebuilt occasionally.
- **Backward-shift deletion** (for linear probing): after emptying a slot, walk forward and shift back any entry
  that's displaced from its home slot, closing the gap. No tombstones, at the cost of a slightly more complex
  erase.

An order book's map sees constant inserts and deletes all day, so tombstone buildup is a real concern; backward
shift avoids it entirely. The practice set includes this as a problem.

### Modern flat maps

Production-quality open-addressing tables add a few tricks:

- **Metadata bytes**: a separate array of one byte per slot holding 7 bits of the hash plus empty/deleted flags.
  Lookups scan 16 metadata bytes at once with SIMD (`_mm_cmpeq_epi8`) and only touch slots whose byte matches.
  This is the design of Abseil's `flat_hash_map` (SwissTable) and Boost's `unordered_flat_map`.
- **Robin Hood hashing**: on insert, an entry that is far from its home slot can displace one that's closer to its
  own, which equalizes probe lengths.
- Storing large values out of line ("node" variants) when pointer stability is needed.

> [!PERF]
> For integer keys and small values, typical benchmarks show `absl::flat_hash_map` or
> `boost::unordered_flat_map` at 2–5× the speed of `std::unordered_map` for lookups, and much more for insert/erase
> because there are no allocations. A hand-written linear-probing table specialized for `uint64_t` keys, sized up
> front, is often faster still. C++23's `std::flat_map` is something else: a **sorted vector** of keys and values,
> with O(log n) lookups, great for small, read-mostly maps.

### Exercises

:::exercise Why the empty-slot rule?
In linear probing, a lookup stops with "not found" when it reaches an empty slot. Explain why simply clearing a slot
on erase breaks this, with a three-key example.
:::solution
Suppose keys A, B, C all hash to slot 5 and are inserted in that order: A goes in 5, B probes to 6, C to 7. Now erase
B by marking slot 6 empty. A lookup for C starts at slot 5 (A, no match), moves to 6, finds it empty, and stops:
"not found", although C is in slot 7. Tombstones (slot 6 = "deleted, keep probing") or backward shifting (move C
back into slot 6) both fix it.
:::

---

## 3. Hashing integers well

With a power-of-two table, the slot is the hash's low bits. So the hash must mix **all** the key's bits into the low
bits. Order IDs often have structure: sequential numbers, or a venue code in the top bits and a counter in the
bottom, or IDs that are multiples of 256. An identity hash maps structured keys to clumps of adjacent or identical
slots.

Good, fast integer mixers:

```cpp
// Fibonacci hashing: multiply by 2^64 / phi, take the HIGH bits. One multiply.
inline std::size_t fib_hash(std::uint64_t x, int table_bits) {
    return (x * 0x9E3779B97F4A7C15ULL) >> (64 - table_bits);
}

// A full avalanche mixer (the murmur3 / splitmix64 finalizer). Two multiplies, three shifts.
inline std::uint64_t mix64(std::uint64_t x) {
    x ^= x >> 33; x *= 0xff51afd7ed558ccdULL;
    x ^= x >> 33; x *= 0xc4ceb9fe1a85ec53ULL;
    x ^= x >> 33;
    return x;
}
```

Fibonacci hashing is excellent for sequential and evenly strided keys and costs one multiply. The full mixer is
robust against almost any key structure for a couple of nanoseconds. Both are far cheaper than a cache miss, so
there's little reason to skimp.

> [!WARNING]
> If keys can come from an adversary (an external client choosing order IDs or symbols), a fixed, known hash
> function enables **hash flooding**: they choose keys that all collide, turning O(1) lookups into O(n). Use a
> seeded hash for externally controlled keys. Exchange-assigned IDs from a trusted feed are usually fine.

### String keys

For symbols, the best hash is often no string hash at all. Symbols in most feeds are at most 8 characters, so pack
them into a `uint64_t` and hash that integer:

```cpp
inline std::uint64_t pack_symbol(std::string_view s) {
    std::uint64_t k = 0;
    std::memcpy(&k, s.data(), std::min<std::size_t>(s.size(), 8));
    return k;                       // compare and hash as one integer
}
```

Better still, map symbols to dense integer IDs once, when the feed starts, and use those IDs to index arrays on the
hot path (Chapter 4.1).

### Exercises

:::exercise The bad hash
A table has 1,024 slots and uses `std::hash<std::uint64_t>` (identity) with `slot = hash & 1023`. Order IDs are
assigned as `venue_id << 32 | counter`, where `counter` increments by 1 per order. Is this a problem? What if IDs
increment by 1,024?
:::solution
With a counter incrementing by 1, the low 10 bits cycle through all values, so consecutive orders land in
consecutive slots: well distributed (and even cache-friendly). If IDs increment by 1,024, the low 10 bits are
**always the same**, so every key maps to one slot and the table degenerates into a linear scan. A mixing hash
(Fibonacci or `mix64`) distributes both patterns well.
:::

---

## 4. Choosing a map

| Situation | Choice |
|---|---|
| Off the hot path, simplicity matters | `std::unordered_map` (or `std::map` for ordering) |
| Hot path, general purpose | `absl::flat_hash_map`, `boost::unordered_flat_map`, `ankerl::unordered_dense` |
| Hot path, integer keys, known maximum size | a custom linear-probing table, preallocated, with a mixing hash |
| Small (≤ ~64 entries) | a linear scan over a `std::vector` or `std::array` of pairs |
| Read-mostly, needs ordered iteration | a sorted vector or `std::flat_map` |
| Keys are dense integers | not a map at all: an array indexed by key |
| Need pointers to elements that survive inserts | `std::unordered_map`, or a flat map of indices into a pool |

A few habits help with `std::unordered_map` when you do use it: call `reserve(n)` up front to avoid rehashing; use
`try_emplace` and `find` rather than `operator[]` (which inserts a default value when the key is missing); and avoid
looking the same key up twice (`if (m.count(k)) use(m[k]);` does two lookups).

---

## Summary

- `std::unordered_map` uses separately allocated nodes in bucket chains: an allocation per insert, a free per erase,
  and 2–3 cache misses per lookup. The standard's stability guarantees force this design.
- Open addressing stores entries in one array. Linear probing touches one or two adjacent cache lines per lookup
  and never allocates per element.
- Keep the load factor moderate; preallocate for the known peak so the table never rehashes during trading.
- Erase needs tombstones or backward shifting to keep probe chains intact.
- With power-of-two tables, use a mixing hash (Fibonacci or a murmur-style finalizer). `std::hash` on integers is the
  identity.
- Pack short strings into integers, or better, intern them to dense IDs.

## Key terms

**Separate chaining** · **open addressing** · **linear probing** · **probe sequence** · **load factor** ·
**primary clustering** · **tombstone** · **backward-shift deletion** · **SwissTable** · **Robin Hood hashing** ·
**Fibonacci hashing** · **hash flooding**
