+++
title = "The Cost of Allocation"
minutes = 40
practice_tags = ["memory", "allocators"]
+++

## Why this chapter matters

How much does `malloc(64)` cost? Measured on the test machine with glibc's allocator, it depends enormously on what
happened before:

| Scenario | Cost per `malloc` + `free` pair |
|---|---|
| allocate 64 bytes, free it immediately, repeat | 8.4 ns |
| allocate a million 64-byte blocks, then free them all | 41 ns |
| a million blocks of mixed sizes (16–512 bytes), freed in shuffled order | ~350 ns |
| the allocation needs fresh memory from the OS (page fault, Chapter 5.5) | ~2,500 ns |

The best case is fast. The realistic case, a long-running process with many live objects of different sizes and
lifetimes, is 40× slower, and the worst case hits the kernel. On top of the call itself, freshly allocated blocks are
scattered through memory, so the objects you allocate become cache misses later.

This is why low-latency systems have a simple rule: **no allocation on the hot path**. This chapter explains what an
allocator does, why its cost is so variable, where allocations hide in C++ code, and how to keep them off the
critical path. Chapter 7.5 builds the custom allocators that make that possible.

By the end of this chapter you should be able to:

- explain how a general-purpose allocator like glibc's `malloc` works, and why its cost varies;
- describe fragmentation and its effect on memory use and locality;
- find hidden allocations in C++ code;
- apply the standard techniques for allocation-free hot paths.

---

## 1. What `malloc` does

A general-purpose allocator must handle any size, any lifetime, and any thread, returning memory to a pool when
it's freed and reusing it later. glibc's allocator (ptmalloc) works roughly like this:

- **Per-thread caches (tcache).** Each thread keeps small singly linked lists of recently freed blocks, one per size
  class up to ~1 KB, a few entries each. A `malloc` that finds a block here pops it, about 20 instructions with no
  locking. That's the 8.4 ns case: free puts the block on the tcache and the next malloc takes it straight back.
- **Arenas and bins.** When the tcache is empty or full, the allocator goes to an **arena** (a heap region with a
  lock; threads are spread across several). Free blocks are kept in **bins** by size. Allocation searches for a
  suitable block, may split a larger one, and free may **coalesce** a block with free neighbours to fight
  fragmentation. Each step touches block headers and bin lists scattered through memory.
- **The top of the heap.** If no free block fits, the allocator carves memory from the top of the heap, growing it
  with `brk` or `mmap` when needed. Fresh memory pages fault on first touch.
- **Large blocks.** Allocations above a threshold (128 KB by default, adjusted dynamically) get their own `mmap`, and
  `free` returns them to the OS with `munmap`. Allocating and freeing a large buffer repeatedly means page faults
  every time.

So the cost of a `malloc` depends on the state of the tcache, the bins and the heap, which depends on everything the
program has allocated and freed before. That's what makes it **unpredictable**, which for latency is worse than slow.

> [!NOTE]
> Alternative allocators (jemalloc, tcmalloc, mimalloc) have better multi-threaded scaling and less fragmentation
> than glibc, and switching to one with `LD_PRELOAD` is often a quick throughput win for allocation-heavy services.
> But they're still general-purpose: their worst cases are still microseconds, so they don't change the rule for
> hot paths.

### Exercises

:::exercise Why so different?
Explain why freeing a million 64-byte blocks in shuffled order and mixed sizes costs 40× more per pair than freeing
each block immediately after allocating it.
:::solution
Immediate free/malloc reuses the same block from the tcache: one hot cache line, no searching, no locking. With a
million live blocks of mixed sizes, the tcache overflows immediately, so most operations go to the arena's bins:
each `free` reads and writes the block's header and its neighbours' headers (to coalesce) at an essentially random
address in ~270 MB of heap, so each is a likely cache miss and TLB miss, plus bin-list manipulation and arena
locking. The work per operation is larger, and every memory touch is cold.
:::

---

## 2. Fragmentation and locality

Over a long run, a general-purpose heap becomes **fragmented**: free memory is split into many small pieces between
live blocks.

- **External fragmentation**: enough total memory is free, but not in one piece large enough for a request, so the
  heap grows anyway. Long-running processes slowly bloat.
- **Internal fragmentation**: blocks are rounded up to size classes, plus header overhead (8–16 bytes per block in
  glibc). A 24-byte allocation uses 32 bytes.

For performance, the more important effect is on **locality**. Objects allocated one after another in a fresh heap
tend to be adjacent. In a fragmented heap they land wherever a free block happened to be, so a linked list or tree
built from them is spread all over memory, and traversing it is a chain of cache misses (Chapter 5.2). That's part
of why a `std::list` or `std::map` gets slower over the course of a trading day even when its size doesn't change.

---

## 3. Where allocations hide

The obvious allocations are `new`, `make_unique` and `make_shared`. The hidden ones are more dangerous:

| Code | Allocates when |
|---|---|
| `std::vector::push_back`, `insert`, `resize`, `emplace_back` | capacity is exceeded |
| `std::string` construction, copy, `+`, `substr`, `to_string` | longer than the SSO buffer (15 chars in libstdc++) |
| `std::map`, `set`, `list`, `unordered_map` insert | **every** insert (node allocation) |
| `std::unordered_map` insert | also when it rehashes |
| `std::function` construction or copy | the callable is bigger than the small buffer (Chapter 2.5) |
| `std::shared_ptr` construction | always (control block), unless from `make_shared` (one allocation) |
| `std::any`, `std::regex`, iostreams, `std::format` | frequently |
| `std::thread` creation, `std::async` | always |
| exceptions | allocating the exception object on `throw` |
| copying any container | always, unless empty |
| `std::deque` push | when a new block is needed |

To find them in a running program, count allocations by replacing the global allocation functions in a test build:

```cpp
#include <atomic>
#include <cstdlib>
#include <new>

inline std::atomic<std::uint64_t> g_allocs{0};

void* operator new(std::size_t n) {
    g_allocs.fetch_add(1, std::memory_order_relaxed);
    if (void* p = std::malloc(n)) return p;
    throw std::bad_alloc{};
}
void operator delete(void* p) noexcept { std::free(p); }
void operator delete(void* p, std::size_t) noexcept { std::free(p); }
```

Then assert in a test that processing a replayed burst of messages, after warm-up, performs **zero** allocations:

```cpp
warm_up(engine, sample_messages);
auto before = g_allocs.load();
replay(engine, test_messages);
assert(g_allocs.load() == before);    // the hot path must not allocate
```

This test catches regressions immediately: someone adds a log line that builds a `std::string`, and CI fails. Tools
like `heaptrack` and `ltrace -e malloc` do similar things without code changes.

### Exercises

:::exercise Spot the allocations
How many heap allocations can this per-message function perform, at worst?

```cpp
void on_fill(const Fill& f) {
    std::string key = f.account + ":" + f.symbol;
    positions_[key] += f.qty;                          // std::unordered_map<std::string, int64_t>
    history_.push_back(f);                             // std::vector<Fill>
    if (f.qty > big_) notify([f] { alert(f); });       // notify(std::function<void()>)
}
```
:::solution
Building `key`: up to 2–3 (each `+` may allocate if the strings exceed the SSO size). `positions_[key]`: one node
allocation if the key is new, plus a copy of the key string inside the node (another allocation if long), plus a
possible rehash. `history_.push_back`: a reallocation when capacity is exceeded. `notify`: the lambda captures a
whole `Fill` (likely more than 16 bytes), so constructing the `std::function` allocates. Worst case: around 7, on
every call where the conditions line up. An allocation-free version would use a precomputed integer key (account
index × instrument index) into a preallocated table, a preallocated ring buffer for history, and a fixed-capacity
callback or a queue of alert records.
:::

---

## 4. Keeping allocation off the hot path

The techniques, roughly in order of how often they're used:

1. **Preallocate at start-up.** Size containers for the expected peak with `reserve`, pre-fault them (Chapter 5.5),
   and never let them grow during trading. If a hard limit is exceeded, that's an error to handle, not a reason to
   allocate.
2. **Reuse instead of re-creating.** Keep scratch buffers as members and `clear()` them (Chapter 4.2). A
   `std::vector` or `std::string` that's cleared keeps its capacity.
3. **Fixed-capacity containers.** `std::array`, ring buffers, and inline vectors with a compile-time capacity
   (Chapter 7.6) never allocate.
4. **Pools for fixed-size objects.** Orders, levels and messages are recycled from a free list of preallocated slots
   (Chapter 7.5): allocation is a pointer pop, a few nanoseconds, always.
5. **Arenas for things with a shared lifetime.** Everything allocated while processing one message (or one session)
   comes from a bump allocator and is released all at once (Chapter 7.5).
6. **Avoid node-based standard containers** on the hot path, or give them a pool or arena allocator through
   `std::pmr` (Chapter 7.5).
7. **Defer.** If something genuinely needs to allocate (formatting a log line, persisting a record), send the raw
   data to another thread and let it allocate there (Chapter 12.4).

The effect can be large even before considering tail latency. Building and destroying a `std::list` of a million
`int64_t`s on the test machine costs 42.8 ns per node with the default allocator, and 10.2 ns per node with a
`std::pmr::monotonic_buffer_resource` (an arena) supplying the nodes: 4× faster for an identical container.

> [!PERF]
> The goal isn't only a faster average. An allocation-free hot path has no allocator lock contention, no page
> faults from heap growth, no fragmentation-induced cache misses developing over the day, and no rare multi-
> microsecond `malloc` calls. Those are the sources of the tail latencies that Chapter 6.4 taught you to measure.

---

## Summary

- `malloc` cost varies from ~8 ns (thread-cache hit) to hundreds of ns (cold bins, coalescing) to microseconds
  (page faults, `mmap`): measured on the test machine.
- Unpredictability is the main problem for latency, along with lock contention and scattered placement of new
  objects.
- Fragmentation wastes memory and destroys locality over long runs.
- Allocations hide in containers, strings, `std::function`, `shared_ptr`, exceptions and copies. Count them with a
  replaced `operator new` and assert zero allocations on the hot path in tests.
- Preallocate, reuse, use fixed-capacity containers, pools and arenas, and defer genuine allocation to another
  thread.

## Key terms

**General-purpose allocator** · **thread cache (tcache)** · **arena** · **bins** · **coalescing** ·
**fragmentation (internal / external)** · **hidden allocation** · **allocation counting** · **preallocation** ·
**pool** · **arena allocator**
