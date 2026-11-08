+++
hours = 6
+++

## Motivation

Every exchange, and every trading system that listens to one, maintains a **limit order book**: the set of all
resting buy orders (*bids*) and sell orders (*asks*), organized by price and, within a price, by arrival time.
A market-data feed is really just a stream of instructions for keeping this book up to date: *add order 123,
cancel order 456, order 789 traded 100 shares*. A strategy decides what to do by looking at the book: the best
bid and ask, the size at each level, the queue ahead of its own order.

This makes the book the most performance-critical data structure in the stack. A busy US equity feed can deliver
millions of messages per second at the open, and **every message updates the book before anything else can
happen**. If your book takes 300 ns per update, you're 300 ns behind every competitor whose book takes 30.

In this project you'll build one twice. First you'll write a clean, obviously-correct version with standard
containers. Then you'll profile it and rebuild the internals for speed, using what you learned about cache lines,
allocation and data layout, without changing the API.

```svg "A book with three bid levels and three ask levels. Each level is a FIFO queue; the hash index finds any order in O(1) for cancels."
<svg viewBox="0 0 700 300" xmlns="http://www.w3.org/2000/svg" role="img"><defs><marker id="arrowhead" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path class="head" d="M0,0 L10,5 L0,10 z"/></marker></defs>
<text class="title" x="20" y="22">Bids (buy), best = highest</text><text class="title" x="370" y="22">Asks (sell), best = lowest</text>
<rect class="f2" x="20" y="36" width="90" height="34" rx="6"/><text class="mono" x="65" y="58" text-anchor="middle">101 · 12</text>
<rect class="box" x="120" y="36" width="56" height="34" rx="6"/><text class="mono" x="148" y="58" text-anchor="middle">#7 5</text>
<rect class="box" x="184" y="36" width="56" height="34" rx="6"/><text class="mono" x="212" y="58" text-anchor="middle">#9 7</text>
<rect class="f2" x="20" y="80" width="90" height="34" rx="6"/><text class="mono" x="65" y="102" text-anchor="middle">100 · 30</text>
<rect class="box" x="120" y="80" width="56" height="34" rx="6"/><text class="mono" x="148" y="102" text-anchor="middle">#2 10</text>
<rect class="box" x="184" y="80" width="56" height="34" rx="6"/><text class="mono" x="212" y="102" text-anchor="middle">#4 15</text>
<rect class="box" x="248" y="80" width="56" height="34" rx="6"/><text class="mono" x="276" y="102" text-anchor="middle">#8 5</text>
<rect class="f2" x="20" y="124" width="90" height="34" rx="6"/><text class="mono" x="65" y="146" text-anchor="middle">98 · 40</text>
<rect class="box" x="120" y="124" width="56" height="34" rx="6"/><text class="mono" x="148" y="146" text-anchor="middle">#1 40</text>
<rect class="f4" x="370" y="36" width="90" height="34" rx="6"/><text class="mono" x="415" y="58" text-anchor="middle">102 · 8</text>
<rect class="box" x="470" y="36" width="56" height="34" rx="6"/><text class="mono" x="498" y="58" text-anchor="middle">#5 8</text>
<rect class="f4" x="370" y="80" width="90" height="34" rx="6"/><text class="mono" x="415" y="102" text-anchor="middle">103 · 25</text>
<rect class="box" x="470" y="80" width="56" height="34" rx="6"/><text class="mono" x="498" y="102" text-anchor="middle">#3 20</text>
<rect class="box" x="534" y="80" width="56" height="34" rx="6"/><text class="mono" x="562" y="102" text-anchor="middle">#6 5</text>
<rect class="f4" x="370" y="124" width="90" height="34" rx="6"/><text class="mono" x="415" y="146" text-anchor="middle">105 · 60</text>
<rect class="box" x="470" y="124" width="56" height="34" rx="6"/><text class="mono" x="498" y="146" text-anchor="middle">#10 60</text>
<path class="arrow" d="M120,176 L300,176"/><text class="muted" x="120" y="196">queue order: first in, first filled</text>
<text class="muted" x="20" y="196">price · total</text>
<rect class="box-strong" x="20" y="222" width="660" height="56" rx="10"/>
<text class="title" x="36" y="246">index: order id → location</text>
<text class="mono" x="36" y="266">#1 → (Buy, 98, node)   #2 → (Buy, 100, node)   #3 → (Sell, 103, node)   …</text>
<text class="muted" x="560" y="196" text-anchor="middle">spread = 102 − 101</text>
</svg>
```

**What you'll practise:** choosing containers by their real costs, iterator and pointer stability, O(1) removal
from the middle of a queue, testing against a reference model, benchmarking, and data-oriented redesign.

> [!NOTE]
> Prices are integer **ticks** (`int64_t`), never `double`. See Chapter 1.3 for why.

---

## The API

You implement this class. The full header, a test suite and a benchmark are in the project folder at
`starter/projects/order-book/`.

```cpp order_book.hpp
using OrderId = std::uint64_t;
using Price   = std::int64_t;   // integer ticks
using Qty     = std::int64_t;

enum class Side : std::uint8_t { Buy, Sell };

struct Level {
    Price         price;
    Qty           qty;     // total resting quantity at this price
    std::uint32_t count;   // number of orders at this price
    bool operator==(const Level&) const = default;
};

class OrderBook {
public:
    // Add a resting limit order at the back of its price level's queue.
    // Returns false (and changes nothing) if `id` already exists or qty <= 0.
    bool add(OrderId id, Side side, Price price, Qty qty);

    // Remove an order entirely. Returns false if `id` is unknown.
    bool cancel(OrderId id);

    // Reduce an order's quantity by `by`. It keeps its place in the queue.
    // Returns false if `id` is unknown or by <= 0. If its quantity reaches 0, it is removed.
    bool reduce(OrderId id, Qty by);

    std::optional<Level>   best_bid() const;
    std::optional<Level>   best_ask() const;
    std::optional<OrderId> front(Side side) const;            // next order to be filled
    Qty                    volume_at(Side side, Price price) const;
    std::vector<Level>     depth(Side side, std::size_t n) const;   // best first
    std::size_t            order_count() const;
};
```

### Required complexity (version 1)

| Operation | Target |
|---|---|
| `add` | O(log L) where L = number of price levels |
| `cancel`, `reduce` | O(1) to find the order, plus O(log L) at worst if a level empties |
| `best_bid`, `best_ask`, `front` | O(1) |
| `depth(n)` | O(n) |

```bash
cd starter/projects/order-book
g++ -std=c++20 -O2 -Wall -Wextra -fsanitize=address,undefined order_book_test.cpp -o test && ./test
g++ -std=c++20 -O2 -march=native order_book_bench.cpp -o bench && ./bench
```

---

## Implementation

Work through the steps in order. Each step lists what to build, and the hints get progressively more specific.
Only open them when you're stuck.

:::step Choose the data structures
Before writing any code, decide how you'll store (a) the price levels on each side, sorted, with fast access to
the best one; (b) the queue of orders inside a level, which needs push-to-back, pop-from-front and **remove from
the middle** (cancels); and (c) a way to find an order from its id alone, since `cancel(id)` doesn't tell you the
side or price.

Write the private members and a one-line justification for each.
:::hint
Sorted map from price to level. For bids you want the *highest* price first. Think about what comparator
`std::map` takes.
:::hint
Removing from the middle of a queue in O(1) needs a structure whose elements don't move and whose positions
you can remember. Which standard container keeps iterators valid when *other* elements are inserted or removed?
:::hint
A reasonable version 1: `std::map<Price, LevelQueue, std::greater<Price>>` for bids,
`std::map<Price, LevelQueue>` for asks, a `std::list<Order>` inside each level, and
`std::unordered_map<OrderId, Locator>` where `Locator` stores the side, the price and the `std::list` iterator.
:::

:::step Implement add
Insert the order at the back of its level, creating the level if needed. Keep the level's aggregate `qty` and
`count` up to date so `best_bid()` doesn't have to sum anything. Reject duplicates and non-positive quantities
*before* modifying anything.
:::hint
`map[price]` default-constructs a level if it doesn't exist. Convenient, but make sure the duplicate check runs
first, or you'll leave an empty level behind when you reject an order.
:::hint
After `push_back`, the iterator to the new element is `std::prev(list.end())`. Store it in the index.
:::

:::step Implement cancel and reduce
`cancel` uses the index to jump straight to the order, unlinks it, fixes the level's aggregates, and **erases the
level if it's now empty**. A stale empty level would make `best_bid()` wrong. `reduce` adjusts quantities in
place without moving the order. Over-reducing removes it.
:::hint
Copy what you need out of the index entry *before* erasing it, since erasing invalidates references into the map.
:::hint
`reduce` can delegate to `cancel` when `by >= qty`. Only the "partial" case needs new code.
:::

:::step Queries: best, front, volume, depth
With the maps sorted the right way, the best level is always `begin()`. `depth` walks forward from `begin()`.
Run the test suite now. The final test replays 200,000 random operations against a simple model; if it fails,
add prints to find the first operation where your book and the model disagree.
:::hint
Writing every query twice (once for bids, once for asks) gets tedious because the two maps have different types.
A small private template helper that takes the side and a generic lambda, `with_side(side, [&](auto& m) {...})`,
removes the duplication.
:::

:::step Measure version 1
Run `order_book_bench`. Record ns/op. For reference, a straightforward version 1 like the one described above runs
at about **310 ns/op** on the test machine (i5-6500T). Then profile it:

```bash
perf stat -e cycles,instructions,cache-misses,L1-dcache-load-misses ./bench
perf record -g ./bench && perf report
```

Where does the time go? Write down your hypothesis before you look.
:::hint
Count the heap allocations per `add`: one for the list node, one for the hash-map node, and sometimes one for a
new map node. Then count the cache misses per operation: the hash bucket, the hash node, the map's tree walk, the
list node. Each one is a potential trip to DRAM (Chapter 5.3).
:::

:::step Version 2: make it fast
Keep the API and the tests, and replace the internals. Aim for a **5× or better** improvement on the benchmark.
Ideas, roughly in order of payoff:

1. **Store orders in a pool.** One big `std::vector<OrderNode>`, with nodes linked by 32-bit indices instead of
   pointers, plus a free list of reusable slots. No allocation per order.
2. **Index price levels by array offset.** Prices cluster near the touch, so a flat array indexed by
   `price - base` gives O(1) level lookup with no tree walk. Track the best price explicitly and rescan when the
   best level empties.
3. **Replace `unordered_map`** with a flat open-addressing hash table, or a direct-indexed vector if ids are
   dense (exchange ids often are).
:::hint
For the intrusive queue, each `OrderNode` holds `prev` and `next` indices; each level holds `head`, `tail`,
`qty` and `count`. Removing a node from the middle is then the classic doubly-linked-list unlink, done with
indices into the pool.
:::hint
When the best bid level empties, find the new best by scanning downward through the array until you reach a
non-empty level. That's usually only a few slots, since books are dense near the touch. For the rare long scan,
a bitmap of non-empty levels plus `std::countl_zero` finds the next set bit 64 levels at a time.
:::hint
Handle prices outside the array's band gracefully: either recenter (rebuild the array around a new base) or fall
back to a `std::map` for far-away levels. Far levels are rarely touched, so the fallback can be slow.
:::

---

## Testing and benchmarking

- The provided suite covers the API contract, FIFO priority, aggregation, depth, and a randomized comparison
  against a model. Keep it green after every change: performance work on a broken book is worthless.
- Run the tests under `-fsanitize=address,undefined` after every refactor. Index-based linked lists are easy to
  corrupt silently.
- Benchmark at `-O2 -march=native`, several runs, and take the minimum. Change one thing at a time and keep a log
  of ns/op after each change.

## Stretch goals

:::exercise Latency distribution
The benchmark reports a mean. Change it to time each operation individually (with `rdtsc` or
`steady_clock`) and print p50, p99, p99.9 and the maximum. Which operations produce the tail?
:::hint
Timing one operation with `steady_clock::now()` costs about 20 ns, which is comparable to what you're measuring.
Time batches of 16 operations, or use `__rdtsc()`, and subtract the measured overhead of an empty timed region.
:::

:::exercise Market-by-price feed
Add a `std::vector<Level> snapshot(std::size_t n)` that returns the top `n` levels on both sides as a single
consolidated view, and keep a sequence number that increments on every change to the top 5 levels. A strategy
can then skip work when the sequence number hasn't changed.
:::

:::exercise Connect it to the parser
Once you've done the **Binary Market Data Parser** project, feed its messages straight into this book and
measure end-to-end messages per second.
:::
