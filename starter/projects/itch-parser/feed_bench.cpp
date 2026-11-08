// Benchmark: parse 10 million messages from memory.
//   g++ -std=c++20 -O2 -march=native feed_bench.cpp -o bench && ./bench
#include "feed.hpp"
#include "encoder.hpp"
#include <chrono>
#include <cstdio>
#include <random>

struct Summer {
    std::uint64_t shares = 0, ids = 0, n = 0;
    void on(const feed::AddOrder& m) { shares += m.shares; ids ^= m.order_id; ++n; }
    void on(const feed::OrderCancel& m) { shares -= m.cancelled_shares; ++n; }
    void on(const feed::OrderExecuted& m) { ids ^= m.match_id; ++n; }
    void on(const feed::OrderDelete& m) { ids ^= m.order_id; ++n; }
    void on_unknown(char, std::span<const std::byte>) { ++n; }
};

int main() {
    feed::test::Writer w;
    std::mt19937_64 rng(3);
    constexpr int N = 10'000'000;
    w.buf.reserve(N * 30);
    for (int i = 0; i < N; ++i) {
        std::uint64_t r = rng();
        switch (r % 4) {
            case 0: w.add(i, r, 'B', std::uint32_t(r >> 40), "SPY", std::uint32_t(r >> 32)); break;
            case 1: w.cancel(i, r, 7); break;
            case 2: w.exec(i, r, 3, r >> 1); break;
            default: w.del(i, r);
        }
    }
    double best = 1e300; Summer keep;
    for (int rep = 0; rep < 5; ++rep) {
        Summer s;
        auto t0 = std::chrono::steady_clock::now();
        auto res = feed::parse(w.buf, s);
        auto t1 = std::chrono::steady_clock::now();
        if (res.messages != std::size_t(N)) { std::printf("parsed %zu of %d messages!\n", res.messages, N); return 1; }
        best = std::min(best, std::chrono::duration<double>(t1 - t0).count()); keep = s;
    }
    std::printf("%d msgs in %.1f ms: %.1f ns/msg, %.0f M msgs/s, %.2f GB/s (checksum %llu)\n", N, best * 1e3,
                best * 1e9 / N, N / best / 1e6, double(w.buf.size()) / best / 1e9,
                (unsigned long long)(keep.shares ^ keep.ids));
}
