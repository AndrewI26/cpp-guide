#include "spsc_queue.hpp"
#include "test_util.hpp"
#include <cstdint>
#include <memory>
#include <string>
#include <thread>
#include <chrono>
#include <cstdio>

static void test_single_thread() {
    SpscQueue<int, 4> q;
    int v = -1;
    EXPECT(!q.try_pop(v) && q.size_approx() == 0);
    EXPECT(q.try_push(1) && q.try_push(2) && q.try_push(3) && q.try_push(4));
    EXPECT(!q.try_push(5));                                  // full at exactly Capacity
    EXPECT(q.size_approx() == 4);
    EXPECT(q.try_pop(v) && v == 1);
    EXPECT(q.try_push(5));
    for (int want : {2, 3, 4, 5}) EXPECT(q.try_pop(v) && v == want);
    EXPECT(!q.try_pop(v));
}

static void test_wraparound() {
    SpscQueue<std::uint64_t, 8> q;
    std::uint64_t next_in = 0, next_out = 0, v;
    bool ok = true;
    for (int round = 0; round < 100000; ++round) {           // many laps around the ring
        int pushes = round % 7 + 1;
        for (int i = 0; i < pushes; ++i) if (q.try_push(next_in)) ++next_in;
        int pops = round % 5 + 1;
        for (int i = 0; i < pops; ++i) if (q.try_pop(v)) { ok &= v == next_out; ++next_out; }
    }
    while (q.try_pop(v)) { ok &= v == next_out; ++next_out; }
    EXPECT(ok && next_in == next_out && next_in > 100000);
}

static void test_non_trivial_type() {
    SpscQueue<std::string, 16> q;
    std::string long_str(100, 'x');
    for (int i = 0; i < 10; ++i) EXPECT(q.try_push(long_str + std::to_string(i)));
    std::string out;
    for (int i = 0; i < 10; ++i) EXPECT(q.try_pop(out) && out == long_str + std::to_string(i));
}

static void test_two_threads() {
    constexpr std::uint64_t N = 5'000'000;
    auto q = std::make_unique<SpscQueue<std::uint64_t, 1024>>();
    std::atomic<bool> abort{false};
    auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(20);
    std::thread producer([&] {
        for (std::uint64_t i = 1; i <= N && !abort.load(std::memory_order_relaxed); ++i)
            while (!q->try_push(i) && !abort.load(std::memory_order_relaxed)) { /* spin */ }
    });
    std::uint64_t expected = 1, v = 0, spins = 0;
    bool in_order = true;
    while (expected <= N) {
        if (q->try_pop(v)) { in_order &= v == expected; ++expected; }
        else if (++spins % 4096 == 0 && std::chrono::steady_clock::now() > deadline) {
            std::printf("two-thread test timed out after receiving %llu of %llu messages\n",
                        (unsigned long long)(expected - 1), (unsigned long long)N);
            abort = true;
            break;
        }
    }
    producer.join();
    EXPECT(!abort.load());
    EXPECT(in_order);
    EXPECT(!q->try_pop(v));
}

int main() {
    test_single_thread();
    test_wraparound();
    test_non_trivial_type();
    test_two_threads();
    return report();
}
