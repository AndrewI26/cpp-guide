// Build:  g++ -std=c++20 -O2 main.cpp quote.cpp -o inl && ./inl
// With LTO: g++ -std=c++20 -O2 -flto main.cpp quote.cpp -o inl_lto && ./inl_lto
#include <chrono>
#include <cstdio>
#include <vector>
#include "quote.hpp"

template <class F> double best_ms(F f) {
    double best = 1e300;
    for (int r = 0; r < 7; ++r) {
        auto t0 = std::chrono::steady_clock::now(); f();
        best = std::min(best, std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - t0).count());
    }
    return best;
}
volatile double sink;

int main() {
    std::vector<Quote> q(1 << 20);
    for (size_t i = 0; i < q.size(); ++i) q[i] = {100.0 + i * 1e-6, 100.01 + i * 1e-6};
    double a = best_ms([&] { double s = 0; for (auto& x : q) s += x.spread_outofline(); sink = s; });
    double b = best_ms([&] { double s = 0; for (auto& x : q) s += x.spread_inline(); sink = s; });
    std::printf("1M quotes  out-of-line: %.3f ms   inline: %.3f ms   (%.1fx)\n", a, b, a / b);
}
