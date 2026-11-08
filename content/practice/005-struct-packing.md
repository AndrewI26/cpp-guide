+++
title = "Pack the Order Struct"
difficulty = "easy"
tags = ["memory", "alignment", "cache"]
+++

The order book stores millions of these, and profiling shows the scans are memory-bound:

```cpp
struct Order {
    bool          is_buy;
    std::int64_t  price_ticks;
    std::uint16_t venue;
    std::int64_t  order_id;
    std::int32_t  qty;
    bool          active;
};   // sizeof == 40 on x86-64
```

**Reorder the fields** (keep every field, name and type) so that `sizeof(Order) == 24`. Then implement
`std::int64_t total_buy_qty(std::span<const Order>)`, which sums `qty` over active buy orders.

## Starter Code

```cpp
#pragma once
#include <cstdint>
#include <span>

struct Order {
    bool          is_buy;
    std::int64_t  price_ticks;
    std::uint16_t venue;
    std::int64_t  order_id;
    std::int32_t  qty;
    bool          active;
};

inline std::int64_t total_buy_qty(std::span<const Order> orders) {
    return 0;  // TODO
}
```

## Hints

- Each member is aligned to its own size. Padding appears wherever a member would start at a misaligned offset.
- Sort the members by alignment, largest first.

## Solution

```cpp
#pragma once
#include <cstdint>
#include <span>

struct Order {
    std::int64_t  price_ticks;   // offset 0
    std::int64_t  order_id;      // 8
    std::int32_t  qty;           // 16
    std::uint16_t venue;         // 20
    bool          is_buy;        // 22
    bool          active;        // 23  -> total 24, no padding
};
static_assert(sizeof(Order) == 24);

inline std::int64_t total_buy_qty(std::span<const Order> orders) {
    std::int64_t total = 0;
    for (const Order& o : orders)
        if (o.is_buy && o.active) total += o.qty;
    return total;
}
```

The original layout wastes 16 of its 40 bytes on padding. Ordering by decreasing alignment packs the fields with
none, so 2.67 orders fit in each cache line instead of 1.6, and a full scan moves 40% less memory. See
Chapter 1.3, §4.

## Tests

```cpp
#include <vector>
int main() {
    CHECK_EQ(sizeof(Order), 24u);
    CHECK_EQ(alignof(Order), 8u);
    std::vector<Order> v(3);
    v[0].is_buy = true;  v[0].active = true;  v[0].qty = 10;
    v[1].is_buy = true;  v[1].active = false; v[1].qty = 20;
    v[2].is_buy = false; v[2].active = true;  v[2].qty = 40;
    CHECK_EQ(total_buy_qty(v), 10);
    Order o{};
    o.price_ticks = 1; o.order_id = 2; o.qty = 3; o.venue = 4; o.is_buy = true; o.active = true;
    CHECK_EQ(o.venue, 4);
    CHECK_EQ(total_buy_qty(std::vector<Order>(1000, o)), 3000);
    return judge::report();
}
```
