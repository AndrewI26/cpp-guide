+++
title = "Event Dispatch with std::variant"
difficulty = "medium"
tags = ["stl", "variant", "finance"]
+++

A strategy consumes a stream of market events. Instead of a class hierarchy with virtual `handle()` methods,
model each event as a `std::variant`. That gives value semantics, contiguous storage and no heap allocation:

```cpp
struct Trade  { std::int64_t price; std::int64_t qty; };
struct Quote  { std::int64_t bid; std::int64_t ask; };
struct Halt   { };
using Event = std::variant<Trade, Quote, Halt>;

struct Summary {
    std::int64_t volume = 0;        // total traded qty (ignore trades while halted)
    std::int64_t last_mid2 = 0;     // bid + ask of the most recent quote (2× the mid, kept integral)
    std::size_t  trades = 0;        // number of trades counted
    bool         halted = false;    // a Halt stops counting trades; quotes still update last_mid2
};
Summary summarize(std::span<const Event> events);
```

Use `std::visit`.

## Starter Code

```cpp
#pragma once
#include <cstdint>
#include <span>
#include <variant>

struct Trade { std::int64_t price; std::int64_t qty; };
struct Quote { std::int64_t bid; std::int64_t ask; };
struct Halt {};
using Event = std::variant<Trade, Quote, Halt>;

struct Summary {
    std::int64_t volume = 0;
    std::int64_t last_mid2 = 0;
    std::size_t trades = 0;
    bool halted = false;
};

inline Summary summarize(std::span<const Event> events) {
    Summary s;
    // TODO
    return s;
}
```

## Hints

- The "overloaded" idiom builds a visitor out of lambdas:
  `template <class... Fs> struct overloaded : Fs... { using Fs::operator()...; };`
- Or write a small struct with three `operator()` overloads that holds a reference to the `Summary`.

## Solution

```cpp
#pragma once
#include <cstdint>
#include <span>
#include <variant>

struct Trade { std::int64_t price; std::int64_t qty; };
struct Quote { std::int64_t bid; std::int64_t ask; };
struct Halt {};
using Event = std::variant<Trade, Quote, Halt>;

struct Summary {
    std::int64_t volume = 0;
    std::int64_t last_mid2 = 0;
    std::size_t trades = 0;
    bool halted = false;
};

template <class... Fs> struct overloaded : Fs... { using Fs::operator()...; };

inline Summary summarize(std::span<const Event> events) {
    Summary s;
    for (const Event& e : events) {
        std::visit(overloaded{
            [&](const Trade& t) { if (!s.halted) { s.volume += t.qty; ++s.trades; } },
            [&](const Quote& q) { s.last_mid2 = q.bid + q.ask; },
            [&](const Halt&)    { s.halted = true; },
        }, e);
    }
    return s;
}
```

`std::visit` compiles to a jump table indexed by the variant's discriminator, similar in cost to a virtual call
but with no heap and no pointer chasing: the events sit contiguously in the array. A `std::vector<Event>` is
`sizeof(Event)` bytes per element (here 24). A `std::vector<std::unique_ptr<EventBase>>` would mean an
allocation and a cache miss per event.

## Tests

```cpp
#include <vector>
int main() {
    std::vector<Event> ev{Quote{100, 102}, Trade{101, 10}, Trade{101, 5}, Quote{99, 103}, Halt{}, Trade{100, 7}, Quote{98, 100}};
    Summary s = summarize(ev);
    CHECK_EQ(s.volume, 15);
    CHECK_EQ(s.trades, 2u);
    CHECK(s.halted);
    CHECK_EQ(s.last_mid2, 198);
    Summary e = summarize(std::vector<Event>{});
    CHECK(e.volume == 0 && !e.halted && e.trades == 0);
    CHECK_EQ(sizeof(Event), 24u);
    return judge::report();
}
```
