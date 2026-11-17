+++
title = "Data-Oriented Design"
minutes = 40
practice_tags = ["data-layout", "cache", "memory"]
+++

## Why this chapter matters

A risk engine models each order as an object:

```cpp
class Order {
public:
    virtual ~Order() = default;
    virtual double exposure() const = 0;
    // id, price, qty, timestamp, trader, venue, side, type, symbol, client reference, fee...
};
std::vector<std::unique_ptr<Order>> orders;
```

Computing total exposure means visiting 4 million orders, each a separate heap allocation reached through a pointer,
each a 64-byte-plus object of which the loop needs 16 bytes, each a virtual call. The code is a textbook example of
object-oriented design, and it runs at the speed of DRAM latency.

**Data-oriented design** starts from a different question. Not "what are the objects in my domain?" but "what data
does each hot operation read and write, and in what order?" Then it lays out the data so those operations stream
through memory. In this chapter's measurements on the test machine, the same computation over the same orders runs
**4.6× faster** after a change of layout alone, and the gap grows when the objects are scattered on the heap.

By the end of this chapter you should be able to:

- describe the costs that object-oriented layouts impose on hot loops;
- analyse a workload by its data access patterns rather than its object model;
- apply the core data-oriented techniques: contiguous storage, hot/cold splitting, processing in batches, and
  replacing pointers with indices;
- decide when data-oriented design is worth its cost in code structure.

---

## 1. What object-oriented layouts cost

None of these is a problem in code that runs occasionally. All of them are problems in a loop over millions of items:

1. **Indirection.** `std::vector<std::unique_ptr<Order>>` or `std::vector<Order*>` stores pointers. Each element
   access is a dependent load to wherever that object was allocated: Chapter 5.2's pointer chase, up to 100 ns per
   object if they're scattered.
2. **Unused bytes in every cache line.** If the loop needs `qty` and `price` (16 bytes) out of a 64-byte object, three
   quarters of every cache line fetched is wasted bandwidth and wasted cache capacity.
3. **Virtual dispatch per element.** An indirect call per object, which blocks inlining and vectorization, and
   mispredicts if types are mixed (Chapters 3.6, 5.4).
4. **One-at-a-time processing.** Methods operate on one object; there's no opportunity to process eight prices with
   one SIMD instruction.
5. **Allocation per object.** Creating and destroying objects one by one on the heap costs ~10–50 ns each and
   fragments memory (Chapter 7.4).

The textbook OO design satisfies all five. The data-oriented version of the same computation fixes all five.

---

## 2. Start from the access pattern

Data-oriented design begins by listing the **hot operations** and, for each, which fields it touches, how often,
and in what order. For an order store in a risk engine, that might be:

| Operation | Frequency | Fields read | Fields written | Access pattern |
|---|---|---|---|---|
| total exposure by side | every 10 ms | `side`, `price`, `qty` | — | scan all |
| apply fill | per execution message | `id` → index, `qty` | `qty` | random by ID |
| audit / reporting | end of day | everything | — | scan all |
| new order | per message | — | everything | append |

The scan touches three small fields of every order. The audit touches everything but runs once a day. So the hot
data is `side`, `price`, `qty`, and everything else is **cold**.

That observation leads directly to the layout. Measured on the test machine with 4 million orders, each 64 bytes in
the object layout:

| Computation | Array of 64-byte structs | Separate arrays of the needed fields | Speedup |
|---|---|---|---|
| sum of `qty` | 3.84 ns/order | 0.84 ns/order | 4.6× |
| buy notional (`side`, `price`, `qty`) | 3.81 ns/order | 1.37 ns/order | 2.8× |

Both versions are contiguous, with no pointers or virtual calls. The difference is only how many useless bytes each
cache line carries. The object-pointer version would be slower still. Chapter 7.2 takes this layout question apart
in detail.

> [!PERF]
> A useful estimate: a scan's cost is roughly **bytes touched ÷ memory bandwidth**. The 64-byte layout moves
> 256 MB for 4 million orders; at the ~13 GB/s a single core streams from DRAM on the test machine (Chapter 5.2),
> that's ~20 ms, about 5 ns/order if nothing else limited it. The `qty`-only array moves 32 MB. When a loop is
> bandwidth bound, shrinking what it touches is the optimization.

---

## 3. The core techniques

### Store data contiguously, by value

Replace `std::vector<std::unique_ptr<T>>` with `std::vector<T>` wherever the type is known. If you need
polymorphism over a closed set of types, use a `std::variant` per element, or better, a separate vector per type
(section 3.4).

### Split hot and cold data

Move rarely used fields out of the hot struct into a parallel array or a separate table indexed the same way:

```cpp
struct OrderHot {                     // 24 bytes: touched by every scan and every fill
    std::int64_t price;
    std::int64_t qty;
    std::uint32_t trader;
    Side side;
};

struct OrderCold {                    // touched by audit, reporting, cancels-by-client-ref
    std::uint64_t exchange_id, client_ref, timestamp;
    std::array<char, 8> symbol;
    double fee;
    std::uint16_t venue;
};

struct OrderStore {
    std::vector<OrderHot>  hot;       // same index i refers to the same order in both
    std::vector<OrderCold> cold;
};
```

This alone often gives most of the benefit with little disruption to code.

### Replace pointers with indices

Pointers are 8 bytes, invalidated by reallocation (Chapter 4.2), meaningless across processes or after
serialization, and every one is a potential cache miss to an unpredictable address. A 32-bit index into a vector is
half the size, survives reallocation, can be stored in shared memory or written to disk, and keeps related data in
one array where the prefetcher can help:

```cpp
struct Level { std::int64_t price; std::int64_t total_qty; std::uint32_t first_order; };   // index, not pointer
struct OrderNode { std::uint32_t next; std::uint32_t prev; std::uint32_t hot_index; };     // an intrusive list by index
```

The order-book project's fast version is built this way.

### Process in batches, by type

Instead of handling each message completely before the next (a virtual call or `switch` per message), group work by
type and run tight loops:

```cpp
// Instead of: for (auto& m : messages) m.apply(book);
for (const auto& a : adds)    book.add(a);       // one tight loop, one code path, predictable branches
for (const auto& c : cancels) book.cancel(c);
for (const auto& e : execs)   book.execute(e);
```

Batching keeps the instruction cache and branch predictor focused on one code path, lets independent operations
overlap their cache misses (Chapter 5.2's memory-level parallelism), and opens the door to SIMD. It only works where
ordering between batches doesn't matter, which is often true for analytics and backtesting but rarely true for
processing a live order book, where order of events is the whole point.

### Exercises

:::exercise Identify hot and cold
A `Position` struct has: instrument ID, quantity, average price, realized P&L, unrealized P&L, last mark price, the
trader's name (`std::string`), account code (`std::string`), creation time and a free-text comment
(`std::string`). The hot operation recomputes unrealized P&L for every position on every price update. Which fields
are hot, and roughly how big are the hot and the full structs?
:::solution
Hot: instrument ID (to find the mark), quantity, average price, unrealized P&L (written), and possibly last mark.
That's about 4–5 eight-byte fields: 32–40 bytes. The full struct, with three `std::string`s at 32 bytes each in
libstdc++, plus the rest, is around 160 bytes, and the strings may point to further heap data. Splitting gives a
4–5× reduction in bytes touched by the revaluation loop.
:::

---

## 4. When to apply it

Data-oriented design has costs: code that operates on parallel arrays is less self-describing than code that calls
methods on objects, invariants spread across several containers, and adding a field means deciding where it lives.
Apply it where the payoff is:

- **Hot loops over many items**: books, positions, risk scans, backtest event processing, feature computation.
- **Latency-critical paths** where cache misses are the dominant cost.
- **Not** configuration, start-up, admin interfaces, or anything that runs rarely. There, clarity wins.

A good pattern is to keep a clean, object-like *interface* (functions like `book.add(order)`, `positions.mark(id,
price)`) while the *storage* behind it is data-oriented. Callers don't need to know that an order is spread across
three arrays.

> [!NOTE]
> Data-oriented design is standard practice in game engines (where it was popularized, notably by Mike Acton and the
> "entity component system" architecture) and in high-performance trading. The principles are the same: the
> hardware is the platform, and the data's layout is the program's most important design decision.

---

## Summary

- Object-oriented layouts (pointers to individually allocated objects with virtual methods) impose indirection,
  wasted cache-line bytes, indirect calls, one-at-a-time processing and per-object allocation.
- Design from access patterns: which fields each hot operation reads and writes, how often, in what order.
- Core techniques: contiguous storage by value, hot/cold splitting, indices instead of pointers, batching work by
  type.
- Measured on the test machine: summing one field over 4 M orders was 4.6× faster from a dedicated array than from
  64-byte structs.
- Keep object-like interfaces over data-oriented storage, and apply it where loops are hot.

## Key terms

**Data-oriented design** · **access pattern** · **hot / cold data** · **hot/cold splitting** · **indirection** ·
**index instead of pointer** · **batch processing** · **bandwidth bound** · **entity component system**
