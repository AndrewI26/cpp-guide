// Benchmarks for the "Cache Lines, Locality and Prefetching" chapter.
//   g++ -std=c++20 -O2 cache_bench.cpp -o cache_bench && ./cache_bench
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <list>
#include <numeric>
#include <random>
#include <vector>

template <class F>
double time_ms(F&& f, int reps = 5) {          // best of `reps` runs, in milliseconds
    double best = 1e300;
    for (int r = 0; r < reps; ++r) {
        auto t0 = std::chrono::steady_clock::now();
        f();
        auto t1 = std::chrono::steady_clock::now();
        best = std::min(best, std::chrono::duration<double, std::milli>(t1 - t0).count());
    }
    return best;
}

volatile int64_t sink;                          // stops the compiler deleting the work

constexpr int N = 4096;

int64_t sum_rows(const std::vector<int>& m) {
    int64_t s = 0;
    for (int r = 0; r < N; ++r)
        for (int c = 0; c < N; ++c) s += m[r * N + c];
    return s;
}

int64_t sum_cols(const std::vector<int>& m) {
    int64_t s = 0;
    for (int c = 0; c < N; ++c)
        for (int r = 0; r < N; ++r) s += m[r * N + c];
    return s;
}

int64_t strided(const std::vector<int>& a, int stride) {   // touch every `stride`-th int
    int64_t s = 0;
    for (size_t i = 0; i < a.size(); i += stride) s += a[i];
    return s;
}

int main() {
    // 1. row-major vs column-major traversal of a 64 MB matrix
    std::vector<int> m(size_t(N) * N, 1);
    double rows = time_ms([&] { sink = sum_rows(m); });
    double cols = time_ms([&] { sink = sum_cols(m); });
    std::printf("matrix %dx%d   rows: %7.2f ms   cols: %7.2f ms   (%.1fx slower)\n", N, N, rows, cols, cols / rows);

    // 2. stride experiment: same array, touching fewer elements, same number of lines until stride > 16
    std::vector<int> big(64 << 20 >> 2, 1);    // 64 MB
    for (int stride : {1, 2, 4, 8, 16, 32, 64, 128}) {
        double t = time_ms([&] { sink = strided(big, stride); });
        std::printf("stride %2d  accesses: %9zu   time: %6.2f ms   ns/access: %5.2f\n", stride,
                    big.size() / stride, t, t * 1e6 / double(big.size() / stride));
    }

    // 3. vector vs list (fresh) vs list (shuffled nodes)
    constexpr int L = 1 << 20;
    std::vector<int64_t> vec(L, 1);
    std::list<int64_t> fresh(L, 1);
    std::list<int64_t> shuffled;
    {
        // allocate nodes, then relink them in random order so traversal jumps around the heap
        std::vector<std::list<int64_t>> singles(L);
        for (auto& s : singles) s.push_back(1);
        std::vector<int> order(L);
        std::iota(order.begin(), order.end(), 0);
        std::shuffle(order.begin(), order.end(), std::mt19937{42});
        for (int i : order) shuffled.splice(shuffled.end(), singles[i]);
    }
    auto sum = [](const auto& c) { int64_t s = 0; for (auto x : c) s += x; return s; };
    double tv = time_ms([&] { sink = sum(vec); });
    double tf = time_ms([&] { sink = sum(fresh); });
    double ts = time_ms([&] { sink = sum(shuffled); });
    std::printf("sum 2^20 int64:  vector %.3f ns/elem   list(fresh) %.3f ns/elem   list(shuffled) %.3f ns/elem\n",
                tv * 1e6 / L, tf * 1e6 / L, ts * 1e6 / L);

    // 4. padded 64-byte orders vs a dense array of the hot field
    struct Order { int64_t id; int64_t price; int32_t qty; char pad[44]; };
    static_assert(sizeof(Order) == 64);
    std::vector<Order> orders(1 << 22, Order{1, 100, 1, {}});
    std::vector<int32_t> qty(1 << 22, 1);
    double ta = time_ms([&] { int64_t s = 0; for (auto& o : orders) s += o.qty; sink = s; });
    double tb = time_ms([&] { int64_t s = 0; for (auto q : qty) s += q; sink = s; });
    std::printf("sum qty over 4M orders:  64-byte structs %.2f ms   dense int32 array %.2f ms   (%.1fx)\n",
                ta, tb, ta / tb);
}
