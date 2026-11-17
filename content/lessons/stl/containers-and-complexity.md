+++
title = "Containers and Their Real Costs"
minutes = 40
practice_tags = ["stl", "data-structures", "cache"]
+++

## Why this chapter matters

An order book stores the orders at each price level in a `std::list<Order>`, because "list insertion and deletion
are O(1) and vector's are O(n)." A benchmark of the most common operation, walking a level to find where to fill,
shows the list version is **ten times slower** than a `std::vector<Order>` at typical level depths, even though
the vector does more "work" per cancel.

Big-O notation describes how cost **grows** as input gets large. It deliberately ignores constant factors. On a
modern CPU, those constant factors are dominated by memory access patterns, and they vary by 100× or more between
containers. For the sizes trading systems actually use (dozens of levels, hundreds of orders per level, thousands
of instruments), the constant factors usually decide the winner.

By the end of this chapter you should be able to:

- describe the memory layout of each standard container;
- estimate the real cost of common operations, including allocations and cache misses;
- explain why contiguous containers usually beat node-based ones even when Big-O says otherwise;
- choose a container for a given access pattern, and know when to replace the standard one.

---

## 1. Two families: contiguous and node-based

Every standard container is one of two basic designs.

**Contiguous** containers store elements next to each other in one block of memory:

- `std::array<T, N>`: fixed size, stored inline (on the stack or inside the enclosing object).
- `std::vector<T>`: dynamic size, one heap block.
- `std::string`: like `vector<char>`, plus small-string optimization (Chapter 4.5).
- `std::deque<T>`: a sequence of fixed-size blocks (contiguous within a block, not overall).

**Node-based** containers allocate each element separately, in a **node** that also holds pointers to other nodes:

- `std::list<T>`: doubly linked list; each node has `prev`, `next` and the element.
- `std::forward_list<T>`: singly linked list.
- `std::map`, `std::set`, `std::multimap`, `std::multiset`: red-black trees; each node has a parent, two
  children, a colour and the element.
- `std::unordered_map`, `std::unordered_set`: an array of buckets, each pointing to a linked list of nodes
  (Chapter 4.4).

```layout "A vector<int64_t> of four prices is one 32-byte block: four elements per half cache line. A list<int64_t> is four separate 24-byte heap nodes (plus allocator overhead), each potentially anywhere in memory."
title: std::vector<std::int64_t> (one heap block)
row: 32
p0:8:1 p1:8:1 p2:8:1 p3:8:1

title: one std::list<std::int64_t> node (one of four separate heap blocks)
row: 32
prev:8:2 next:8:2 value:8:1 malloc_header:8:6
```

The difference matters because of how the hardware works (Chapter 5.3): memory moves in 64-byte cache lines, and
the CPU prefetches sequential accesses automatically. Walking a vector of `int64_t` touches one new cache line per
eight elements, and the prefetcher has it ready before you need it. Walking a list touches one new cache line per
element, at an address the CPU can't know until it has loaded the previous node. Each step can be a full trip to
DRAM: around 80 ns, versus under 1 ns for the next vector element.

> [!PERF]
> A useful rule of thumb on modern hardware: a sequential scan of contiguous memory processes several GB/s;
> a pointer-chasing walk over nodes scattered in memory processes one node per ~5–100 ns, depending on whether
> they happen to be in cache. That's a 10–100× difference before any algorithmic considerations.

---

## 2. The real cost of common operations

Here's what each container actually does for common operations. "Alloc" means a call into the heap allocator
(roughly 20–100 ns, Chapter 7.4).

| Container | Lookup by position | Find by value | Insert/erase at end | Insert/erase in middle | Iterate n elements |
|---|---|---|---|---|---|
| `vector` | O(1), one access | O(n) scan, fast | amortized O(1); occasional realloc | O(n) shift, as one `memmove` | n sequential accesses |
| `deque` | O(1), two accesses | O(n) scan | O(1); occasional block alloc | O(n) | mostly sequential |
| `list` | O(n) walk | O(n) walk, slow | O(1) + alloc/free | O(1) + alloc/free, *if you already have the iterator* | n dependent loads, scattered |
| `map` / `set` | — | O(log n), log n dependent loads | O(log n) + alloc | O(log n) + alloc | n dependent loads |
| `unordered_map` | — | O(1) avg: hash, bucket load, node load | O(1) + alloc | O(1) + alloc | n dependent loads |

Three things stand out:

1. **"O(1) insert in the middle" for a list assumes you already have an iterator there.** Finding the position is
   O(n) pointer chasing. For a vector, finding is a fast scan (or a binary search), and the shift is a single
   `memmove`, which moves tens of GB per second. For hundreds of elements, the vector usually wins outright.
2. **Every node-based insert allocates, and every erase frees.** On a hot path, allocation is often the dominant
   cost, and it's unpredictable (Chapter 7.4).
3. **A `std::map` lookup in a map of 1,000 elements is about 10 dependent loads**, each to a node allocated at a
   different time and so probably on a different cache line. A sorted `std::vector` with binary search does the
   same 10 comparisons with better locality, and a linear scan of a small vector may beat both.

### Exercises

:::exercise Estimate it
You walk 1,000 orders summing quantities. Estimate the time if they're in (a) a `std::vector<Order>` with 32-byte
`Order`s, (b) a `std::list<Order>` whose nodes are scattered randomly in a large heap. Assume L1 hits take ~1 ns,
DRAM accesses ~80 ns, and the prefetcher fully hides sequential access latency.
:::solution
(a) 32 KB of contiguous data, 500 cache lines, all prefetched: the loop is limited by compute and L1 bandwidth,
roughly 0.5–1 ns per order, so about **0.5–1 µs**. (b) Each node is a likely cache miss and the next address isn't
known until the current node arrives, so the misses can't overlap: about 1,000 × 80 ns = **80 µs**. Two orders of
magnitude, for identical Big-O. (In practice, nodes allocated consecutively are often adjacent in memory, so a
fresh list does better; a list that has seen many inserts and erases over a trading day does worse.)
:::

---

## 3. Measured: vector vs list vs map

The classic experiment: insert *n* random integers into a sequence, keeping it sorted, then remove them one by one
from random positions. Big-O says the list should win: O(1) insert and erase versus vector's O(n) shifting. Yet on
every modern machine, the vector wins at every size up to hundreds of thousands of elements, because *finding* the
position dominates and the vector finds it with a fast linear scan or binary search, while the list chases pointers.

A version relevant to order books: a price level holds the resting orders at one price, in time priority. The
operations are append at the back (new order), remove from the front (fill), and remove by ID (cancel, anywhere).

```cpp
// Candidate A: std::list<Order>, plus a hash map from order ID to list iterator for O(1) cancel
// Candidate B: std::vector<Order>, cancel by scanning for the ID and erasing
// Candidate C: std::vector<Order>, cancel by marking a tombstone (qty = 0), compacted occasionally
```

Rough per-operation costs to expect at a level depth of 50 orders on a desktop x86 CPU (estimates; measure
your own with Chapter 6.1's techniques):

| Operation | A: list + map | B: vector, erase | C: vector, tombstone |
|---|---|---|---|
| append | ~60 ns (two allocations) | ~2 ns | ~2 ns |
| fill from front | ~40 ns (two frees) | ~15 ns (shift 49 × 32 B) | ~2 ns (advance a head index) |
| cancel by ID | ~30 ns (hash lookup + unlink + two frees) | ~25 ns (scan + shift) | ~15 ns (scan) |

The "O(1)" list wins nothing. Real order books use designs closer to C, or a vector-backed intrusive list with
indices instead of pointers. The order book project builds one.

> [!WARNING]
> Benchmarks like this are sensitive to element size, depth, and the allocator. A list of 4 KB elements with
> frequent middle insertions really can beat a vector, because shifting 4 KB elements is expensive. Measure with
> your real sizes. The point isn't "lists are always slow"; it's "Big-O alone doesn't decide."

---

## 4. Choosing a container

A decision procedure that works for most trading code:

1. **Default to `std::vector`.** Contiguous, cache-friendly, cheapest to iterate, and almost always fastest for
   fewer than a few thousand elements regardless of the operation mix.
2. **Fixed or bounded size known at compile time?** `std::array`, or a fixed-capacity inline vector (Chapter 7.6).
   No heap at all.
3. **Need lookup by key?**
   - Small (< ~100) or rarely changing: a sorted `std::vector` with `std::lower_bound`, or a linear scan.
   - Large and hot: an open-addressing hash map (Chapter 4.4), not `std::unordered_map`.
   - Need ordered iteration and frequent inserts: `std::map` is acceptable off the hot path; on it, consider a
     sorted vector, a B-tree, or a domain-specific structure (an array indexed by price tick for an order book).
4. **FIFO queue?** `std::deque`, or better, a ring buffer over a vector or array.
5. **Need stable addresses** (pointers to elements that survive insertions)? That's the legitimate reason for
   node-based containers. Alternatives: `std::deque` (stable on push/pop at the ends), a vector of indices into a
   pool, or `std::unique_ptr` elements.
6. **`std::list`**: almost never. When you genuinely need O(1) splice and stable iterators, consider an
   **intrusive** list over pooled nodes instead.

### Know the domain shape

Trading data often has structure you can exploit that no general-purpose container knows about:

- Prices are integers in a narrow band around the current price, so an order book can be an **array indexed by
  tick offset**, giving O(1) level lookup with no hashing and no tree.
- Order IDs are often assigned sequentially by the exchange, so a table indexed by `id - base` beats any hash map.
- Instrument counts are fixed at start-up, so instrument data can live in a vector indexed by a dense integer ID
  assigned at load time.

### Exercises

:::exercise Pick a container
Choose a container (or design) for each: (a) the 20 most recent trade prices, for a moving average; (b) a lookup
from 8-character symbol to instrument data, built once at start-up, ~5,000 entries, queried per message;
(c) the price levels on one side of a book for a liquid stock, where nearly all activity is within 50 ticks of the
best price; (d) a list of strategy objects created from a config file.
:::solution
(a) A fixed ring buffer over `std::array<std::int64_t, 20>`: no allocation, contiguous. (b) Built once and
read-only: a sorted `std::vector` with binary search, or better, an open-addressing hash map keyed on the symbol
packed into a `uint64_t`. Best of all, resolve symbols to dense integer IDs once at the feed boundary, so the hot
path indexes a vector. (c) An array of levels indexed by `price - base_price`, recentred occasionally, perhaps with
a bitmap of non-empty levels for fast best-price search. (d) `std::vector<std::unique_ptr<Strategy>>`: polymorphic,
created once, rarely iterated in a hot loop; simplicity wins.
:::

---

## Summary

- Containers are either **contiguous** (array, vector, string, mostly deque) or **node-based** (list, map, set,
  unordered containers).
- Big-O ignores constant factors; on modern hardware those are dominated by cache misses and allocations and can
  differ by 100×.
- Contiguous iteration is prefetched and uses every byte of each cache line. Node traversal is a chain of dependent,
  scattered loads.
- Every node-based insert allocates and every erase frees.
- Default to `std::vector`. Prefer sorted vectors, arrays indexed by domain keys, and open-addressing hash maps
  over `map`, `list` and `unordered_map` on hot paths.
- Measure with realistic sizes before concluding anything.

## Key terms

**Contiguous container** · **node-based container** · **constant factor** · **pointer chasing** ·
**dependent load** · **amortized** · **stable addresses** · **intrusive container** · **sorted vector**
