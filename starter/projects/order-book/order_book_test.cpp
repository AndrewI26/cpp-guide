#include "order_book.hpp"
#include "test_util.hpp"
#include <random>
#include <map>

static void test_basics() {
    OrderBook b;
    EXPECT(!b.best_bid() && !b.best_ask());
    EXPECT(b.add(1, Side::Buy, 100, 10));
    EXPECT(b.add(2, Side::Buy, 101, 5));
    EXPECT(b.add(3, Side::Sell, 103, 7));
    EXPECT(b.add(4, Side::Sell, 102, 4));
    EXPECT(!b.add(4, Side::Sell, 105, 1));           // duplicate id
    EXPECT(!b.add(5, Side::Buy, 99, 0));             // non-positive qty
    EXPECT(b.order_count() == 4);
    EXPECT((b.best_bid() == Level{101, 5, 1}));
    EXPECT((b.best_ask() == Level{102, 4, 1}));
}

static void test_aggregation_and_fifo() {
    OrderBook b;
    b.add(10, Side::Buy, 100, 3);
    b.add(11, Side::Buy, 100, 4);
    b.add(12, Side::Buy, 100, 5);
    EXPECT((b.best_bid() == Level{100, 12, 3}));
    EXPECT(b.front(Side::Buy) == OrderId{10});
    EXPECT(b.cancel(10));
    EXPECT(b.front(Side::Buy) == OrderId{11});
    EXPECT(b.reduce(11, 1));                         // keeps priority
    EXPECT(b.front(Side::Buy) == OrderId{11});
    EXPECT(b.volume_at(Side::Buy, 100) == 8);
    EXPECT(b.reduce(11, 100));                       // over-reduce removes
    EXPECT(b.front(Side::Buy) == OrderId{12});
    EXPECT(!b.cancel(11) && !b.reduce(11, 1) && !b.reduce(12, 0));
    EXPECT(b.cancel(12));
    EXPECT(!b.best_bid() && b.order_count() == 0);
    EXPECT(b.add(10, Side::Sell, 200, 1));           // ids can be reused after removal
}

static void test_depth() {
    OrderBook b;
    for (int i = 0; i < 10; ++i) {
        b.add(100 + i, Side::Buy, 1000 - i, 10 + i);
        b.add(200 + i, Side::Sell, 1001 + i, 20 + i);
    }
    b.add(300, Side::Buy, 998, 1);
    auto bids = b.depth(Side::Buy, 3);
    EXPECT(bids.size() == 3);
    EXPECT((bids == std::vector<Level>{{1000, 10, 1}, {999, 11, 1}, {998, 13, 2}}));
    auto asks = b.depth(Side::Sell, 2);
    EXPECT((asks == std::vector<Level>{{1001, 20, 1}, {1002, 21, 1}}));
    EXPECT(b.depth(Side::Sell, 100).size() == 10);
    b.cancel(100);
    EXPECT(b.best_bid()->price == 999);
    EXPECT(b.volume_at(Side::Buy, 1000) == 0);
}

// Random operations checked against a slow but obviously-correct model.
static void test_random_against_model() {
    OrderBook b;
    struct M { Side side; Price px; Qty qty; };
    std::map<OrderId, M> model;
    std::mt19937_64 rng(7);
    for (int step = 0; step < 200000; ++step) {
        int op = rng() % 10;
        OrderId id = rng() % 2000;
        if (op < 5) {
            Side s = rng() % 2 ? Side::Buy : Side::Sell;
            Price px = s == Side::Buy ? 1000 - Price(rng() % 50) : 1001 + Price(rng() % 50);
            Qty q = 1 + Qty(rng() % 100);
            bool ok = b.add(id, s, px, q);
            EXPECT(ok == !model.count(id));
            if (ok) model[id] = {s, px, q};
        } else if (op < 8) {
            bool ok = b.cancel(id);
            EXPECT(ok == (model.erase(id) == 1));
        } else {
            Qty by = 1 + Qty(rng() % 60);
            bool ok = b.reduce(id, by);
            auto it = model.find(id);
            EXPECT(ok == (it != model.end()));
            if (it != model.end()) { it->second.qty -= by; if (it->second.qty <= 0) model.erase(it); }
        }
        if (step % 997 == 0) {
            EXPECT(b.order_count() == model.size());
            std::map<Price, Qty> bids, asks;
            for (auto& [_, m] : model) (m.side == Side::Buy ? bids : asks)[m.px] += m.qty;
            auto bb = b.best_bid(), ba = b.best_ask();
            EXPECT(bids.empty() ? !bb : (bb && bb->price == bids.rbegin()->first && bb->qty == bids.rbegin()->second));
            EXPECT(asks.empty() ? !ba : (ba && ba->price == asks.begin()->first && ba->qty == asks.begin()->second));
        }
    }
}

int main() {
    test_basics();
    test_aggregation_and_fifo();
    test_depth();
    test_random_against_model();
    return report();
}
