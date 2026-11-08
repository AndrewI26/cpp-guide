// Benchmark: replay a random add/cancel/reduce stream and report ns per operation.
//   g++ -std=c++20 -O2 -march=native order_book_bench.cpp -o bench && ./bench
#include "order_book.hpp"
#include <chrono>
#include <cstdio>
#include <random>
#include <vector>

struct Op { int kind; OrderId id; Side side; Price px; Qty qty; };

int main() {
    std::mt19937_64 rng(1);
    std::vector<Op> ops;
    std::vector<OrderId> live;
    OrderId next = 1;
    for (int i = 0; i < 5'000'000; ++i) {                 // pre-generate so RNG cost isn't measured
        int r = rng() % 100;
        if (r < 50 || live.size() < 1000) {
            Side s = rng() % 2 ? Side::Buy : Side::Sell;
            Price px = s == Side::Buy ? 10'000 - Price(rng() % 200) : 10'001 + Price(rng() % 200);
            ops.push_back({0, next, s, px, 1 + Qty(rng() % 500)});
            live.push_back(next++);
        } else {
            size_t k = rng() % live.size();
            OrderId id = live[k];
            if (r < 85) { ops.push_back({1, id, Side::Buy, 0, 0}); live[k] = live.back(); live.pop_back(); }
            else ops.push_back({2, id, Side::Buy, 0, 1});
        }
    }
    OrderBook book;
    auto t0 = std::chrono::steady_clock::now();
    long checksum = 0;
    for (const Op& op : ops) {
        if (op.kind == 0) book.add(op.id, op.side, op.px, op.qty);
        else if (op.kind == 1) book.cancel(op.id);
        else book.reduce(op.id, op.qty);
        if (auto bb = book.best_bid()) checksum += bb->price;
    }
    auto t1 = std::chrono::steady_clock::now();
    double ns = std::chrono::duration<double, std::nano>(t1 - t0).count() / double(ops.size());
    std::printf("%zu ops, %.1f ns/op (including a best_bid() query after each), checksum %ld, %zu resting\n",
                ops.size(), ns, checksum, book.order_count());
}
