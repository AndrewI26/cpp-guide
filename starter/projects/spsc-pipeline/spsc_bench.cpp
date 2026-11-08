// Benchmarks: throughput and round-trip latency between two threads.
//   g++ -std=c++20 -O2 -march=native -pthread spsc_bench.cpp -o bench && ./bench [cpuA cpuB]
#include "spsc_queue.hpp"
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <memory>
#include <thread>
#include <vector>
#if defined(__linux__)
#include <pthread.h>
static void pin(int cpu) {
    if (cpu < 0) return;
    cpu_set_t set; CPU_ZERO(&set); CPU_SET(cpu, &set);
    pthread_setaffinity_np(pthread_self(), sizeof set, &set);
}
#else
static void pin(int) {}
#endif

using Clock = std::chrono::steady_clock;

int main(int argc, char** argv) {
    int cpu_a = argc > 2 ? std::atoi(argv[1]) : -1, cpu_b = argc > 2 ? std::atoi(argv[2]) : -1;

    // 1. Throughput: push N integers through the queue.
    constexpr std::uint64_t N = 50'000'000;
    auto q = std::make_unique<SpscQueue<std::uint64_t, 4096>>();
    auto t0 = Clock::now();
    std::thread prod([&] { pin(cpu_a); for (std::uint64_t i = 0; i < N; ++i) while (!q->try_push(i)) {} });
    pin(cpu_b);
    std::uint64_t v, sum = 0;
    for (std::uint64_t i = 0; i < N; ++i) { while (!q->try_pop(v)) {} sum += v; }
    prod.join();
    double secs = std::chrono::duration<double>(Clock::now() - t0).count();
    std::printf("throughput: %.1f M msgs/s (%.2f ns/msg)  [checksum %llu]\n", N / secs / 1e6, secs * 1e9 / N,
                (unsigned long long)sum);

    // 2. Round-trip latency: ping-pong one message through two queues.
    auto ping = std::make_unique<SpscQueue<std::uint64_t, 64>>();
    auto pong = std::make_unique<SpscQueue<std::uint64_t, 64>>();
    constexpr int R = 1'000'000;
    std::thread echo([&] { pin(cpu_a); std::uint64_t x; for (int i = 0; i < R; ++i) { while (!ping->try_pop(x)) {} while (!pong->try_push(x)) {} } });
    std::vector<double> rtt(R);
    for (int i = 0; i < R; ++i) {
        auto s = Clock::now();
        while (!ping->try_push(std::uint64_t(i))) {}
        std::uint64_t x; while (!pong->try_pop(x)) {}
        rtt[i] = std::chrono::duration<double, std::nano>(Clock::now() - s).count();
    }
    echo.join();
    std::sort(rtt.begin(), rtt.end());
    auto pct = [&](double p) { return rtt[std::min<std::size_t>(R - 1, std::size_t(p * R))]; };
    std::printf("round trip ns: p50 %.0f  p90 %.0f  p99 %.0f  p99.9 %.0f  max %.0f\n", pct(.5), pct(.9), pct(.99),
                pct(.999), rtt.back());
}
