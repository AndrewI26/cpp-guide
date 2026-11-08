// Project: Limit Order Book. Implement the member functions below.
// Build tests:  g++ -std=c++20 -O2 -Wall -Wextra -fsanitize=address,undefined order_book_test.cpp -o test && ./test
#pragma once
#include <cstddef>
#include <cstdint>
#include <optional>
#include <vector>

using OrderId = std::uint64_t;
using Price   = std::int64_t;   // integer ticks
using Qty     = std::int64_t;

enum class Side : std::uint8_t { Buy, Sell };

struct Level {
    Price         price;
    Qty           qty;     // total resting quantity at this price
    std::uint32_t count;   // number of orders at this price
    bool operator==(const Level&) const = default;
};

class OrderBook {
public:
    // Add a resting limit order at the back of its price level's queue.
    // Returns false (and changes nothing) if `id` already exists or qty <= 0.
    bool add(OrderId id, Side side, Price price, Qty qty);

    // Remove an order entirely. Returns false if `id` is unknown.
    bool cancel(OrderId id);

    // Reduce an order's quantity by `by` (a partial cancel or a fill). The order keeps its
    // place in the queue. If its quantity reaches 0 it is removed.
    // Returns false if `id` is unknown or by <= 0. If by exceeds the quantity, removes the order.
    bool reduce(OrderId id, Qty by);

    // Best (highest) bid and best (lowest) ask, if any.
    std::optional<Level> best_bid() const;
    std::optional<Level> best_ask() const;

    // The order at the front of the queue at the best price on `side` (next to be filled).
    std::optional<OrderId> front(Side side) const;

    // Total resting quantity at `price` on `side` (0 if none).
    Qty volume_at(Side side, Price price) const;

    // Up to `n` best levels on `side`, best first (bids descending, asks ascending).
    std::vector<Level> depth(Side side, std::size_t n) const;

    // Number of resting orders on both sides.
    std::size_t order_count() const;

private:
    // Your data structures go here.
};

// ---- Implement below (or move to order_book.cpp). These stubs compile but fail the tests. ----
inline bool OrderBook::add(OrderId, Side, Price, Qty) { return false; }
inline bool OrderBook::cancel(OrderId) { return false; }
inline bool OrderBook::reduce(OrderId, Qty) { return false; }
inline std::optional<Level> OrderBook::best_bid() const { return std::nullopt; }
inline std::optional<Level> OrderBook::best_ask() const { return std::nullopt; }
inline std::optional<OrderId> OrderBook::front(Side) const { return std::nullopt; }
inline Qty OrderBook::volume_at(Side, Price) const { return 0; }
inline std::vector<Level> OrderBook::depth(Side, std::size_t) const { return {}; }
inline std::size_t OrderBook::order_count() const { return 0; }
