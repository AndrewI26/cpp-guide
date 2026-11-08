// Benchmark: closed-form pricing throughput and Monte Carlo paths per second.
//   g++ -std=c++20 -O2 -march=native pricer_bench.cpp -o bench && ./bench
#include "pricer.hpp"
#include <chrono>
#include <cstdio>
#include <random>
#include <vector>

using Clock = std::chrono::steady_clock;
volatile double sink;

int main() {
    using namespace pricer;
    std::mt19937_64 rng(1);
    std::uniform_real_distribution<double> K(60, 140), vol(0.05, 0.8), T(0.02, 2.0);
    constexpr int N = 2'000'000;
    std::vector<Option> opts(N);
    for (auto& o : opts) o = {rng() % 2 ? OptionType::Call : OptionType::Put, 100.0, K(rng), 0.03, vol(rng), T(rng)};
    std::vector<double> out(N);

    auto t0 = Clock::now();
    double s = 0;
    for (const auto& o : opts) s += bs_price(o);
    auto t1 = Clock::now();
    bs_price_batch(opts, out);
    auto t2 = Clock::now();
    sink = s + out[N / 2];
    std::printf("bs_price:       %.1f ns/option\n", std::chrono::duration<double, std::nano>(t1 - t0).count() / N);
    std::printf("bs_price_batch: %.1f ns/option\n", std::chrono::duration<double, std::nano>(t2 - t1).count() / N);

    Option c{OptionType::Call, 100, 100, 0.05, 0.2, 1.0};
    auto t3 = Clock::now();
    McResult r = mc_price(c, 10'000'000, 1, true);
    auto t4 = Clock::now();
    double secs = std::chrono::duration<double>(t4 - t3).count();
    std::printf("mc_price:       %.1f M paths/s  (price %.4f ± %.4f vs exact %.4f)\n", 1e7 / secs / 1e6, r.price,
                r.std_error, bs_price(c));
}
