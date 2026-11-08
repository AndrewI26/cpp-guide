+++
title = "Match an Incoming Limit Order"
difficulty = "hard"
tags = ["order-book", "algorithms", "finance"]
+++

An exchange's matching engine takes an incoming order and trades it against resting orders on the opposite side,
at the **best price first**, and within a price **first come, first served** (price-time priority). Any quantity
left over rests in the book at the order's limit price.

```cpp
struct Fill { std::uint64_t resting_id; std::int64_t price; std::int64_t qty; };

class MatchingBook {
public:
    // Submit a limit order. Returns the fills it generated, in execution order.
    // Buy orders match asks priced <= limit; sell orders match bids priced >= limit.
    // Fills execute at the RESTING order's price. Leftover quantity rests at `limit`.
    std::vector<Fill> submit(std::uint64_t id, bool is_buy, std::int64_t limit, std::int64_t qty);
    std::int64_t best_bid() const;   // 0 if no bids
    std::int64_t best_ask() const;   // 0 if no asks
    std::int64_t depth_at(bool is_buy, std::int64_t price) const;
};
```

**Example:** asks rest at 101×5 (id 1), 101×5 (id 2) and 102×10 (id 3). A buy for 12 @ 102 fills id 1 for 5 @
101, id 2 for 5 @ 101, and id 3 for 2 @ 102. Nothing rests, and 102×8 remains on the ask side.

## Starter Code

```cpp
#pragma once
#include <cstdint>
#include <deque>
#include <functional>
#include <map>
#include <vector>

struct Fill { std::uint64_t resting_id; std::int64_t price; std::int64_t qty; };

class MatchingBook {
public:
    std::vector<Fill> submit(std::uint64_t id, bool is_buy, std::int64_t limit, std::int64_t qty) { return {}; }
    std::int64_t best_bid() const { return 0; }
    std::int64_t best_ask() const { return 0; }
    std::int64_t depth_at(bool is_buy, std::int64_t price) const { return 0; }
};
```

## Hints

- Bids: `std::map<int64_t, std::deque<Resting>, std::greater<>>` (best = `begin()`). Asks: an ascending map.
- Write the matching loop once, as a template over the opposite side's map type, with a predicate for "this price
  crosses my limit".
- Remove a price level as soon as its queue empties, so `begin()` is always a live level.

## Solution

```cpp
#pragma once
#include <algorithm>
#include <cstdint>
#include <deque>
#include <functional>
#include <map>
#include <vector>

struct Fill { std::uint64_t resting_id; std::int64_t price; std::int64_t qty; };

class MatchingBook {
    struct Resting { std::uint64_t id; std::int64_t qty; };
public:
    std::vector<Fill> submit(std::uint64_t id, bool is_buy, std::int64_t limit, std::int64_t qty) {
        std::vector<Fill> fills;
        if (is_buy) {
            qty = match(asks_, qty, fills, [&](std::int64_t px) { return px <= limit; });
            if (qty > 0) bids_[limit].push_back({id, qty});
        } else {
            qty = match(bids_, qty, fills, [&](std::int64_t px) { return px >= limit; });
            if (qty > 0) asks_[limit].push_back({id, qty});
        }
        return fills;
    }
    std::int64_t best_bid() const { return bids_.empty() ? 0 : bids_.begin()->first; }
    std::int64_t best_ask() const { return asks_.empty() ? 0 : asks_.begin()->first; }
    std::int64_t depth_at(bool is_buy, std::int64_t price) const {
        return is_buy ? sum(bids_, price) : sum(asks_, price);
    }
private:
    template <class Side, class Crosses>
    static std::int64_t match(Side& side, std::int64_t qty, std::vector<Fill>& fills, Crosses crosses) {
        while (qty > 0 && !side.empty() && crosses(side.begin()->first)) {
            auto& [px, queue] = *side.begin();
            while (qty > 0 && !queue.empty()) {
                Resting& r = queue.front();
                std::int64_t traded = std::min(qty, r.qty);
                fills.push_back({r.id, px, traded});
                qty -= traded;
                r.qty -= traded;
                if (r.qty == 0) queue.pop_front();
            }
            if (queue.empty()) side.erase(side.begin());
        }
        return qty;
    }
    template <class Side>
    static std::int64_t sum(const Side& side, std::int64_t price) {
        auto it = side.find(price);
        if (it == side.end()) return 0;
        std::int64_t s = 0;
        for (const auto& r : it->second) s += r.qty;
        return s;
    }
    std::map<std::int64_t, std::deque<Resting>, std::greater<>> bids_;
    std::map<std::int64_t, std::deque<Resting>> asks_;
};
```

Price priority comes from walking the opposite map from `begin()`, and time priority from the FIFO `deque`
within each level. Executing at the resting price is standard exchange behaviour: the aggressor gets any price
improvement. The **Matching Engine** project builds a fast version with order IDs, cancels and market and IOC
orders.

## Tests

```cpp
int main() {
    MatchingBook b;
    CHECK(b.submit(1, false, 101, 5).empty());
    CHECK(b.submit(2, false, 101, 5).empty());
    CHECK(b.submit(3, false, 102, 10).empty());
    CHECK(b.best_ask() == 101 && b.depth_at(false, 101) == 10);
    auto f = b.submit(10, true, 102, 12);
    CHECK_EQ(f.size(), 3u);
    if (f.size() == 3) {
        CHECK(f[0].resting_id == 1 && f[0].price == 101 && f[0].qty == 5);
        CHECK(f[1].resting_id == 2 && f[1].price == 101 && f[1].qty == 5);
        CHECK(f[2].resting_id == 3 && f[2].price == 102 && f[2].qty == 2);
    }
    CHECK(b.best_ask() == 102 && b.depth_at(false, 102) == 8 && b.best_bid() == 0);
    auto g = b.submit(11, true, 101, 4);          // doesn't cross: rests
    CHECK(g.empty() && b.best_bid() == 101 && b.depth_at(true, 101) == 4);
    auto h = b.submit(12, false, 100, 6);         // sells into the 101 bid, rest 2 @ 100
    CHECK(h.size() == 1 && h[0].resting_id == 11 && h[0].price == 101 && h[0].qty == 4);
    CHECK(b.best_ask() == 100 && b.depth_at(false, 100) == 2 && b.best_bid() == 0);
    auto k = b.submit(13, true, 105, 100);        // sweeps everything
    CHECK(k.size() == 2 && k[0].price == 100 && k[1].price == 102);
    CHECK(b.best_ask() == 0 && b.best_bid() == 105 && b.depth_at(true, 105) == 90);
    return judge::report();
}
```
