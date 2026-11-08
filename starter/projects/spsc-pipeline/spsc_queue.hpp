// Project: Lock-Free Market Data Pipeline. Implement SpscQueue below.
// Build tests (run under both sanitizer sets):
//   g++ -std=c++20 -O2 -Wall -Wextra -fsanitize=address,undefined -pthread spsc_test.cpp -o test && ./test
//   g++ -std=c++20 -O2 -Wall -Wextra -fsanitize=thread -pthread spsc_test.cpp -o test_tsan && ./test_tsan
#pragma once
#include <array>
#include <atomic>
#include <cstddef>

// A bounded, wait-free, single-producer single-consumer queue.
//  * Exactly one thread may call try_push, and exactly one (other) thread may call try_pop.
//  * Holds up to Capacity elements. Capacity must be a power of two >= 2.
//  * T must be default-constructible and copy/move-assignable.
template <class T, std::size_t Capacity>
class SpscQueue {
    static_assert(Capacity >= 2 && (Capacity & (Capacity - 1)) == 0, "Capacity must be a power of two");

public:
    // Producer only. Returns false if the queue is full.
    bool try_push(const T& value);

    // Consumer only. Returns false if the queue is empty; otherwise moves the front element into `out`.
    bool try_pop(T& out);

    // Safe to call from either thread; the result may be stale by the time you use it.
    std::size_t size_approx() const;

    static constexpr std::size_t capacity() { return Capacity; }

private:
    // Your members go here.
};

// ---- Implement below. These stubs compile but fail the tests. ----
template <class T, std::size_t C> bool SpscQueue<T, C>::try_push(const T&) { return false; }
template <class T, std::size_t C> bool SpscQueue<T, C>::try_pop(T&) { return false; }
template <class T, std::size_t C> std::size_t SpscQueue<T, C>::size_approx() const { return 0; }
