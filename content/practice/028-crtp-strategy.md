+++
title = "Static Polymorphism with CRTP"
difficulty = "medium"
tags = ["templates", "static-polymorphism"]
+++

A backtester calls `strategy.on_tick(price)` hundreds of millions of times. With a virtual function, every call
is an indirect jump the compiler can't inline. The **Curiously Recurring Template Pattern** (CRTP) gives you the
"base class calls derived hook" structure with zero runtime dispatch.

Implement `StrategyBase<Derived>` with a non-virtual `tick(price)` that:

1. increments `ticks_seen()`;
2. calls `Derived::on_tick(price)`, which returns a signed order quantity (0 = do nothing);
3. adds that quantity to `position()`.

Then implement two strategies:

- `Momentum`: buys 1 when the price is above the previous price, sells 1 when below (returns 0 on the first tick
  and when unchanged).
- `MeanRevert(threshold)`: keeps a running mean of all prices seen *including* the current one; sells 1 if
  `price > mean + threshold`, buys 1 if `price < mean - threshold`, else 0.

## Starter Code

```cpp
#pragma once
#include <cstdint>

template <class Derived>
class StrategyBase {
public:
    void tick(double price) {
        // TODO: count the tick, call the derived on_tick, update the position
    }
    std::int64_t position() const { return position_; }
    std::int64_t ticks_seen() const { return ticks_; }
private:
    std::int64_t position_ = 0, ticks_ = 0;
};

class Momentum : public StrategyBase<Momentum> {
public:
    std::int64_t on_tick(double price) { return 0; }  // TODO
};

class MeanRevert : public StrategyBase<MeanRevert> {
public:
    explicit MeanRevert(double threshold) : threshold_(threshold) {}
    std::int64_t on_tick(double price) { return 0; }  // TODO
private:
    double threshold_;
};
```

## Hints

- Inside the base: `static_cast<Derived&>(*this).on_tick(price)`. The cast is resolved at compile time.
- `Momentum` needs to remember the previous price and whether it has seen one.
- Because there are no virtual functions, `sizeof(Momentum)` contains no vtable pointer.

## Solution

```cpp
#pragma once
#include <cstdint>

template <class Derived>
class StrategyBase {
public:
    void tick(double price) {
        ++ticks_;
        position_ += static_cast<Derived&>(*this).on_tick(price);
    }
    std::int64_t position() const { return position_; }
    std::int64_t ticks_seen() const { return ticks_; }
private:
    std::int64_t position_ = 0, ticks_ = 0;
};

class Momentum : public StrategyBase<Momentum> {
public:
    std::int64_t on_tick(double price) {
        std::int64_t q = 0;
        if (has_prev_) q = price > prev_ ? 1 : price < prev_ ? -1 : 0;
        prev_ = price;
        has_prev_ = true;
        return q;
    }
private:
    double prev_ = 0;
    bool has_prev_ = false;
};

class MeanRevert : public StrategyBase<MeanRevert> {
public:
    explicit MeanRevert(double threshold) : threshold_(threshold) {}
    std::int64_t on_tick(double price) {
        ++n_;
        mean_ += (price - mean_) / static_cast<double>(n_);
        if (price > mean_ + threshold_) return -1;
        if (price < mean_ - threshold_) return 1;
        return 0;
    }
private:
    double threshold_;
    double mean_ = 0;
    std::int64_t n_ = 0;
};
```

`StrategyBase<Momentum>::tick` is a separate function from `StrategyBase<MeanRevert>::tick`, each with the
derived call inlined directly. You lose the ability to put different strategies in one container, but if the
backtester itself is a template on the strategy type, the whole tick path compiles into one tight loop.

## Tests

```cpp
#include <type_traits>
int main() {
    Momentum m;
    for (double p : {100.0, 101.0, 102.0, 101.0, 101.0, 103.0}) m.tick(p);
    CHECK_EQ(m.ticks_seen(), 6);
    CHECK_EQ(m.position(), 1 + 1 - 1 + 0 + 1);
    MeanRevert r(1.0);
    r.tick(100);   // mean 100      -> 0
    r.tick(100);   // mean 100      -> 0
    r.tick(104);   // mean 101.33   -> sell
    r.tick(98);    // mean 100.5    -> buy
    r.tick(100);   // mean 100.4    -> 0
    CHECK_EQ(r.position(), 0);
    CHECK_EQ(r.ticks_seen(), 5);
    MeanRevert r2(0.5);
    for (double p : {10.0, 20.0, 30.0}) r2.tick(p);   // means 10, 15, 20 -> 0, -1, -1
    CHECK_EQ(r2.position(), -2);
    CHECK(!std::is_polymorphic_v<Momentum>);
    return judge::report();
}
```
