+++
hours = 6
+++

## Motivation

A typical trading process splits its work across threads pinned to dedicated cores. One thread reads the network
and parses market data. Another runs the strategy. A third handles order entry or logging. They have to pass
messages to each other millions of times per second, and every nanosecond spent on that handoff is a nanosecond
of latency on every single tick.

A `std::mutex`-protected `std::queue` is the obvious answer, and it's far too slow. Locking costs tens of
nanoseconds uncontended. Under contention it can put a thread to sleep, and waking it costs **microseconds**.
The queue also allocates.

The standard answer in low-latency systems is a **single-producer, single-consumer (SPSC) ring buffer**: a
fixed array plus two counters, with no locks, no allocation, and no waiting. Because exactly one thread writes
each counter, a handful of carefully ordered atomic loads and stores is all the synchronization needed. Done
well, a message crosses between cores in well under 100 ns. It's the most widely used concurrent data structure
in trading, and building one properly teaches you the C++ memory model, cache coherence and false sharing.

```svg "The ring: the producer writes at tail and the consumer reads at head. Both counters only ever increase, and slot = counter mod capacity."
<svg viewBox="0 0 700 220" xmlns="http://www.w3.org/2000/svg" role="img"><defs><marker id="arrowhead" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path class="head" d="M0,0 L10,5 L0,10 z"/></marker></defs>
<text class="title" x="20" y="24">buf[8]   (Capacity = 8)</text>
<rect class="box" x="20" y="60" width="76" height="46" rx="6"/><text class="mono" x="58" y="88" text-anchor="middle">·</text>
<rect class="box" x="100" y="60" width="76" height="46" rx="6"/><text class="mono" x="138" y="88" text-anchor="middle">·</text>
<rect class="f2" x="180" y="60" width="76" height="46" rx="6"/><text class="mono" x="218" y="88" text-anchor="middle">m11</text>
<rect class="f2" x="260" y="60" width="76" height="46" rx="6"/><text class="mono" x="298" y="88" text-anchor="middle">m12</text>
<rect class="f2" x="340" y="60" width="76" height="46" rx="6"/><text class="mono" x="378" y="88" text-anchor="middle">m13</text>
<rect class="f2" x="420" y="60" width="76" height="46" rx="6"/><text class="mono" x="458" y="88" text-anchor="middle">m14</text>
<rect class="box" x="500" y="60" width="76" height="46" rx="6"/><text class="mono" x="538" y="88" text-anchor="middle">·</text>
<rect class="box" x="580" y="60" width="76" height="46" rx="6"/><text class="mono" x="618" y="88" text-anchor="middle">·</text>
<text class="muted mono" x="58" y="124" text-anchor="middle">0</text><text class="muted mono" x="138" y="124" text-anchor="middle">1</text><text class="muted mono" x="218" y="124" text-anchor="middle">2</text><text class="muted mono" x="298" y="124" text-anchor="middle">3</text><text class="muted mono" x="378" y="124" text-anchor="middle">4</text><text class="muted mono" x="458" y="124" text-anchor="middle">5</text><text class="muted mono" x="538" y="124" text-anchor="middle">6</text><text class="muted mono" x="618" y="124" text-anchor="middle">7</text>
<path class="arrow" d="M218,176 L218,112"/><text x="218" y="196" text-anchor="middle"><tspan class="title">head = 10</tspan></text><text class="muted" x="218" y="212" text-anchor="middle">consumer reads slot 10 &amp; 7 = 2</text>
<path class="arrow" d="M538,176 L538,112"/><text x="538" y="196" text-anchor="middle"><tspan class="title">tail = 14</tspan></text><text class="muted" x="538" y="212" text-anchor="middle">producer writes slot 14 &amp; 7 = 6</text>
<text class="muted" x="660" y="40" text-anchor="end">size = tail − head = 4</text>
</svg>
```

**What you'll practise:** `std::atomic`, acquire/release ordering, the happens-before relation, false sharing
and `alignas`, thread pinning, ThreadSanitizer, and measuring latency distributions rather than averages.

> [!NOTE]
> Read Chapters 11.1–11.3 (atomics, the memory model, the SPSC ring buffer) alongside this project. This page
> tells you *what* to build. The chapters explain *why* each ordering is correct.

---

## The API

The starter files are in `starter/projects/spsc-pipeline/`: the header, tests (with a watchdog, so a broken
queue fails instead of hanging), and a benchmark that measures throughput and round-trip latency.

```cpp spsc_queue.hpp
// A bounded, wait-free, single-producer single-consumer queue.
//  * Exactly one thread may call try_push, and exactly one (other) thread may call try_pop.
//  * Holds up to Capacity elements. Capacity must be a power of two >= 2.
//  * T must be default-constructible and copy/move-assignable.
template <class T, std::size_t Capacity>
class SpscQueue {
    static_assert(Capacity >= 2 && (Capacity & (Capacity - 1)) == 0, "Capacity must be a power of two");
public:
    bool try_push(const T& value);        // producer only; false if full
    bool try_pop(T& out);                 // consumer only; false if empty
    std::size_t size_approx() const;      // either thread; may be stale
    static constexpr std::size_t capacity() { return Capacity; }
};
```

```bash
cd starter/projects/spsc-pipeline
g++ -std=c++20 -O2 -Wall -Wextra -fsanitize=address,undefined -pthread spsc_test.cpp -o test && ./test
g++ -std=c++20 -O2 -Wall -Wextra -fsanitize=thread -pthread spsc_test.cpp -o test_tsan && ./test_tsan
g++ -std=c++20 -O2 -march=native -pthread spsc_bench.cpp -o bench && ./bench 2 3   # pin to CPUs 2 and 3
```

---

## Implementation

:::step A single-threaded ring buffer
Ignore threads for a moment. Build the ring with a `std::array<T, Capacity>` and two `std::size_t` counters,
`head` (next slot to read) and `tail` (next slot to write). Make `test_single_thread`, `test_wraparound` and
`test_non_trivial_type` pass.
:::hint
If the counters only ever increase, and you map them to slots with `counter & (Capacity - 1)`, then
`tail - head` is always the number of elements: `0` means empty, `Capacity` means full. You never need a
separate "full" flag or a wasted slot. (Unsigned wraparound after 2⁶⁴ operations is still correct, because the
subtraction wraps too.)
:::hint
`x & (Capacity - 1)` equals `x % Capacity` only because Capacity is a power of two, which is exactly what the
`static_assert` enforces. It's a single AND instead of a division.
:::

:::step Make it thread-safe with atomics
Now two threads run concurrently. The producer writes `tail` and the consumer writes `head`; each only *reads*
the other's counter. Make both counters `std::atomic<std::size_t>` and choose a memory order for every load and
store. Get `test_two_threads` passing under **TSan**.
:::hint
Think about what must be visible before what. The producer writes the element into the slot, *then* publishes
it by advancing `tail`. The consumer must not see the new `tail` without also seeing the element. Which ordering
on the store, paired with which ordering on the load, gives exactly that guarantee?
:::hint
`tail_.store(t + 1, std::memory_order_release)` in the producer pairs with
`tail_.load(std::memory_order_acquire)` in the consumer: everything written before the release is visible after
the acquire. The same pattern applies in reverse for `head`, so the producer doesn't overwrite a slot the
consumer is still reading.
:::hint
Each thread reads its *own* counter with `memory_order_relaxed`. Nobody else writes it, so no synchronization is
needed for that load.
:::

:::step Eliminate false sharing
Run the benchmark and note the throughput. Now look at your class's memory layout: where do `head` and `tail`
sit relative to each other? Fix it and measure again.
:::hint
If `head` and `tail` share a 64-byte cache line, every write by one core invalidates that line in the other
core's cache, even though they're different variables. The line ping-pongs between cores on every operation.
That's **false sharing** (Chapter 10.3).
:::hint
`alignas(64)` on each counter puts them on separate lines. Also think about what sits *next to* each counter: the
end of the buffer array, or a neighbouring object in memory.
:::

```layout "Left: head and tail share one cache line, so every push and pop bounces it between cores. Right: each counter owns its line."
title: before: one shared line (false sharing)
row: 64
head:8:2 tail:8:4 pad:48

title: after: alignas(64) on each counter
row: 64
head:8:2 cached_tail:8:3 pad:48
tail:8:4 cached_head:8:5 pad:48
```

:::step Cache the other side's counter
Even without false sharing, every `try_push` *reads* `head`, a line the consumer keeps writing, so it still
causes a coherence miss on nearly every call. Reduce how often each side touches the other's line.
:::hint
The producer only needs the real `head` when its *last known* value says the queue is full. Keep a
producer-local `cached_head`, and refresh it from the atomic only in that case. The consumer does the same with
a `cached_tail`. Put each cached copy on the same cache line as the counter its owner writes.
:::

:::step Measure latency properly
Run the benchmark's ping-pong test, pinned to two physical cores. Look at the whole distribution, not just the
median. A reference implementation with all of the steps above measured, on the test machine (i5-6500T, two
pinned cores): **p50 270 ns, p99 294 ns, p99.9 329 ns round trip** (so roughly 135 ns one way, including two
`steady_clock` reads). Unpinned, p50 was 300 ns and the maximum was over 30 µs.
:::hint
The max is usually much larger than p99.9. Interrupts, the OS scheduler, or another process running on your
core cause that. On a tuned trading box, cores are isolated (`isolcpus`, `nohz_full`) so nothing else ever runs
there (Chapter 10.4).
:::hint
Try pinning both threads to two *hyperthreads of the same physical core*, if your CPU has them (check
`lscpu -e`). They share L1 and L2, so the handoff gets faster, but each thread now competes for the core's
execution resources.
:::

---

## Stretch goals

:::exercise Batch operations
Add `std::size_t try_push_n(const T* items, std::size_t n)` and `try_pop_n(T* out, std::size_t max)` that move
several elements with a single counter update. Measure throughput with batches of 16. Why is it so much faster?
:::hint
The expensive part is the cross-core cache-line traffic for the counters, not copying the elements. Batching
pays that cost once per batch instead of once per element.
:::

:::exercise Any T
Remove the default-constructible requirement: store raw, aligned storage (`alignas(T) std::byte[sizeof(T)]`),
construct elements in place with `std::construct_at`, and destroy them on pop and in the destructor. Add an
`emplace(Args&&...)`.
:::

:::exercise The full pipeline
Build a two-thread pipeline: thread A generates synthetic quotes (with the **parser** project if you've done
it), stamps each one with `rdtsc`, and pushes it; thread B pops each quote, updates a running VWAP, and records
`rdtsc() - stamp`. Print the latency percentiles. Then deliberately put a 200-byte struct in the queue instead of
a 32-byte one, and explain the change.
:::
