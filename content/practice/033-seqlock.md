+++
title = "Seqlock for Top-of-Book Snapshots"
difficulty = "hard"
tags = ["concurrency", "atomics"]
+++

The market-data thread updates the top of book (bid, ask, bid size, ask size) millions of times per second.
Several strategy threads read it. A mutex would make the **writer** wait for readers, which is unacceptable. A
**seqlock** never blocks the writer: readers detect that a write happened during their read and retry.

```cpp
struct Top { std::int64_t bid, ask, bid_qty, ask_qty; };

class SeqLock {
public:
    void store(const Top& t);    // single writer thread only
    Top load() const;            // any number of reader threads; returns a consistent snapshot
};
```

**Consistency:** a reader must never return a mix of two different writes. The test's writer always stores
values satisfying `ask == bid + 1`, `bid_qty == bid * 2` and `ask_qty == bid * 3`, and the readers check these
invariants on every snapshot.

To be data-race-free under the C++ memory model (and silent under ThreadSanitizer), store each field in a
`std::atomic<std::int64_t>` accessed with relaxed ordering, and place fences correctly.

## Starter Code

```cpp
#pragma once
#include <atomic>
#include <cstdint>

struct Top { std::int64_t bid, ask, bid_qty, ask_qty; };

class SeqLock {
public:
    void store(const Top& t) {
        bid_ = t.bid; ask_ = t.ask; bid_qty_ = t.bid_qty; ask_qty_ = t.ask_qty;   // BUG: torn reads possible
    }
    Top load() const { return {bid_, ask_, bid_qty_, ask_qty_}; }
private:
    std::int64_t bid_ = 0, ask_ = 0, bid_qty_ = 0, ask_qty_ = 0;
};
```

## Hints

- Keep a sequence counter. The writer makes it **odd** before writing and **even** again after.
- Reader: read seq1 (spin while it's odd), read the fields, then read seq2. If seq1 != seq2, retry.
- Ordering. Writer: `seq.store(s+1, relaxed); atomic_thread_fence(release); write fields (relaxed); seq.store(s+2, release)`.
  Reader: `s1 = seq.load(acquire); read fields (relaxed); atomic_thread_fence(acquire); s2 = seq.load(relaxed)`.

## Solution

```cpp
#pragma once
#include <atomic>
#include <cstdint>

struct Top { std::int64_t bid, ask, bid_qty, ask_qty; };

class SeqLock {
public:
    void store(const Top& t) {
        std::uint64_t s = seq_.load(std::memory_order_relaxed);
        seq_.store(s + 1, std::memory_order_relaxed);              // odd: write in progress
        std::atomic_thread_fence(std::memory_order_release);       // field stores can't move above this
        f_[0].store(t.bid, std::memory_order_relaxed);
        f_[1].store(t.ask, std::memory_order_relaxed);
        f_[2].store(t.bid_qty, std::memory_order_relaxed);
        f_[3].store(t.ask_qty, std::memory_order_relaxed);
        seq_.store(s + 2, std::memory_order_release);              // even: publishes the fields
    }

    Top load() const {
        while (true) {
            std::uint64_t s1 = seq_.load(std::memory_order_acquire);
            if (s1 & 1) continue;                                   // writer active
            Top t{f_[0].load(std::memory_order_relaxed), f_[1].load(std::memory_order_relaxed),
                  f_[2].load(std::memory_order_relaxed), f_[3].load(std::memory_order_relaxed)};
            std::atomic_thread_fence(std::memory_order_acquire);   // field loads can't move below this
            if (seq_.load(std::memory_order_relaxed) == s1) return t;
        }
    }
private:
    alignas(64) std::atomic<std::uint64_t> seq_{0};
    std::atomic<std::int64_t> f_[4] = {};
};
```

The writer never waits. Readers retry only if a write overlapped their read, which is rare when reads are short.
The release fence keeps a reader from seeing new field values alongside an old even sequence number. The acquire
fence keeps the reader's second sequence load from being satisfied before its field loads. Relaxed atomics for
the fields cost the same as plain loads and stores on x86, but make the code race-free by the standard. (On
x86 the fences compile to nothing; they only constrain the compiler.)

## Tests

```cpp
#include <thread>
#include <vector>
int main() {
    SeqLock sl;
    sl.store({1, 2, 2, 3});
    Top t = sl.load();
    CHECK(t.bid == 1 && t.ask == 2 && t.bid_qty == 2 && t.ask_qty == 3);
    std::atomic<bool> stop{false};
    std::atomic<long> torn{0}, reads{0};
    std::vector<std::thread> readers;
    for (int r = 0; r < 3; ++r)
        readers.emplace_back([&] {
            long local_reads = 0;
            while (!stop.load(std::memory_order_relaxed)) {
                Top x = sl.load();
                if (x.ask != x.bid + 1 || x.bid_qty != x.bid * 2 || x.ask_qty != x.bid * 3) torn.fetch_add(1);
                ++local_reads;
            }
            reads.fetch_add(local_reads);
        });
    for (std::int64_t i = 1; i <= 2'000'000; ++i) sl.store({i, i + 1, i * 2, i * 3});
    stop = true;
    for (auto& th : readers) th.join();
    CHECK_EQ(torn.load(), 0);
    CHECK(reads.load() > 0);
    Top last = sl.load();
    CHECK_EQ(last.bid, 2'000'000);
    return judge::report();
}
```
