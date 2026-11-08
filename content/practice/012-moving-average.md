+++
title = "O(1) Simple Moving Average"
difficulty = "medium"
tags = ["data-structures", "finance"]
+++

A strategy keeps a simple moving average (SMA) of the last `N` prices, updated on every tick. Recomputing
`N` additions per tick is wasteful. Implement a class with **O(1)** `push` and **no allocation after
construction**:

```cpp
class MovingAverage {
public:
    explicit MovingAverage(std::size_t window);   // window >= 1
    double push(double price);                    // add a price, return the average of the last min(count, window)
    std::size_t size() const;                     // prices currently in the window
};
```

**Example:** window 3, pushing `1, 2, 3, 4` returns `1, 1.5, 2, 3`.

## Starter Code

```cpp
#pragma once
#include <cstddef>
#include <vector>

class MovingAverage {
public:
    explicit MovingAverage(std::size_t window) {}
    double push(double price) { return 0.0; }
    std::size_t size() const { return 0; }
};
```

## Hints

- Keep a running sum. When the window is full, subtract the price that falls out.
- A fixed-size circular buffer (a `std::vector` sized once, plus a head index) holds the window.
- A running sum of doubles accumulates rounding error over millions of updates. One fix is to recompute the sum
  from the buffer every `N` pushes.

## Solution

```cpp
#pragma once
#include <cstddef>
#include <vector>

class MovingAverage {
public:
    explicit MovingAverage(std::size_t window) : buf_(window, 0.0) {}

    double push(double price) {
        if (count_ == buf_.size()) sum_ -= buf_[head_];
        else ++count_;
        buf_[head_] = price;
        sum_ += price;
        head_ = head_ + 1 == buf_.size() ? 0 : head_ + 1;
        if (head_ == 0) {                          // once per lap, recompute to stop drift
            sum_ = 0.0;
            for (std::size_t i = 0; i < count_; ++i) sum_ += buf_[i];
        }
        return sum_ / static_cast<double>(count_);
    }

    std::size_t size() const { return count_; }

private:
    std::vector<double> buf_;
    std::size_t head_ = 0, count_ = 0;
    double sum_ = 0.0;
};
```

Each push does a constant amount of work, plus an occasional O(N) resummation that averages out to O(1) per
push. The resummation bounds the floating-point drift of the add/subtract running sum. Using
`head + 1 == size ? 0 : head + 1` instead of `%` avoids a division on every tick.

## Tests

```cpp
#include <cmath>
int main() {
    MovingAverage m(3);
    CHECK_NEAR(m.push(1), 1.0, 1e-12);
    CHECK_NEAR(m.push(2), 1.5, 1e-12);
    CHECK_NEAR(m.push(3), 2.0, 1e-12);
    CHECK_NEAR(m.push(4), 3.0, 1e-12);
    CHECK_EQ(m.size(), 3u);
    MovingAverage one(1);
    CHECK_NEAR(one.push(5), 5.0, 1e-12);
    CHECK_NEAR(one.push(7), 7.0, 1e-12);
    MovingAverage big(1000);
    double last = 0;
    for (int i = 0; i < 2'000'000; ++i) last = big.push(1e6 + (i % 1000) * 1e-3);
    CHECK_NEAR(last, 1e6 + 0.4995, 1e-6);
    return judge::report();
}
```
