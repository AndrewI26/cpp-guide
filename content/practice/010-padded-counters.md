+++
title = "Per-Thread Counters Without False Sharing"
difficulty = "easy"
tags = ["cache", "concurrency", "alignment"]
+++

Each of `N` worker threads counts the messages it processes in its own slot of an array, so there's no data race
and no atomic read-modify-write. It's still slow: adjacent counters share a cache line, and every increment
invalidates that line in the other cores' caches (**false sharing**).

Define `PaddedCounter` so that **each counter occupies its own 64-byte cache line**, then implement
`count_in_parallel`, which starts `threads` threads that each increment their own counter `per_thread` times,
and returns the total.

## Starter Code

```cpp
#pragma once
#include <cstdint>
#include <thread>
#include <vector>

struct PaddedCounter {
    std::uint64_t value = 0;   // TODO: give each counter its own cache line
};

inline std::uint64_t count_in_parallel(int threads, std::uint64_t per_thread) {
    std::vector<PaddedCounter> counters(threads);
    // TODO: start threads; thread i increments counters[i].value per_thread times; join; sum.
    return 0;
}
```

## Hints

- `alignas(64)` on the struct raises its alignment, which also rounds its `sizeof` up to 64.
- `std::vector` respects over-aligned types since C++17.
- Use `std::jthread` (it joins automatically), or join your `std::thread`s before summing.

## Solution

```cpp
#pragma once
#include <cstdint>
#include <thread>
#include <vector>

struct alignas(64) PaddedCounter {
    std::uint64_t value = 0;
};
static_assert(sizeof(PaddedCounter) == 64);

inline std::uint64_t count_in_parallel(int threads, std::uint64_t per_thread) {
    std::vector<PaddedCounter> counters(threads);
    {
        std::vector<std::jthread> pool;
        for (int t = 0; t < threads; ++t)
            pool.emplace_back([&counters, t, per_thread] {
                auto& c = counters[t].value;
                for (std::uint64_t i = 0; i < per_thread; ++i) {
                    c = c + 1;
                    asm volatile("" ::: "memory");   // keep the store in the loop for the demonstration
                }
            });
    }   // jthreads join here
    std::uint64_t total = 0;
    for (auto& c : counters) total += c.value;
    return total;
}
```

With `alignas(64)` each counter owns a whole line, so the cores never contend. Without it, 8 counters share one
line, and the loop can run 10× slower or more on multi-core machines. (The empty `asm` statement just stops the
optimizer from collapsing the loop into a single addition, so you can see the effect. Real code would keep a
local count and write it once at the end, which avoids the problem entirely.)

## Tests

```cpp
int main() {
    CHECK_EQ(sizeof(PaddedCounter), 64u);
    CHECK_EQ(alignof(PaddedCounter), 64u);
    CHECK_EQ(count_in_parallel(4, 100000), 400000u);
    CHECK_EQ(count_in_parallel(1, 5), 5u);
    std::vector<PaddedCounter> v(3);
    CHECK(reinterpret_cast<std::uintptr_t>(&v[1]) % 64 == 0);
    return judge::report();
}
```
