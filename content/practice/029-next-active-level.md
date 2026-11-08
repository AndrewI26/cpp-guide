+++
title = "Find the Next Price Level with a Bitmap"
difficulty = "medium"
tags = ["bit-manipulation", "order-book", "cache"]
+++

An array-based order book indexes price levels by tick offset. When the best bid level empties, you must find
the next non-empty level below it. Scanning one level at a time can be slow when the book is sparse. Instead,
keep a **bitmap** with one bit per level and use bit tricks to skip 64 empty levels per instruction.

```cpp
class LevelBitmap {
public:
    explicit LevelBitmap(std::size_t levels);
    void set(std::size_t i);
    void clear(std::size_t i);
    bool test(std::size_t i) const;
    // Highest set index <= i, or -1 if none. (Next bid level at or below i.)
    std::int64_t find_prev(std::size_t i) const;
    // Lowest set index >= i, or -1 if none. (Next ask level at or above i.)
    std::int64_t find_next(std::size_t i) const;
};
```

## Starter Code

```cpp
#pragma once
#include <bit>
#include <cstddef>
#include <cstdint>
#include <vector>

class LevelBitmap {
public:
    explicit LevelBitmap(std::size_t levels) : n_(levels), words_((levels + 63) / 64, 0) {}
    void set(std::size_t i) {}
    void clear(std::size_t i) {}
    bool test(std::size_t i) const { return false; }
    std::int64_t find_prev(std::size_t i) const { return -1; }
    std::int64_t find_next(std::size_t i) const { return -1; }
private:
    std::size_t n_;
    std::vector<std::uint64_t> words_;
};
```

## Hints

- Level `i` lives in word `i / 64`, at bit `i % 64`.
- For `find_next`: mask off the bits below `i` in its word (`word & (~0ull << (i % 64))`). If anything is left,
  `std::countr_zero` gives the lowest set bit. Otherwise move on to the next non-zero word.
- For `find_prev`: keep bits ≤ `i % 64` (careful: shifting by 64 is UB), then use `std::countl_zero` to find the
  highest set bit.

## Solution

```cpp
#pragma once
#include <bit>
#include <cstddef>
#include <cstdint>
#include <vector>

class LevelBitmap {
public:
    explicit LevelBitmap(std::size_t levels) : n_(levels), words_((levels + 63) / 64, 0) {}
    void set(std::size_t i) { words_[i >> 6] |= bit(i); }
    void clear(std::size_t i) { words_[i >> 6] &= ~bit(i); }
    bool test(std::size_t i) const { return (words_[i >> 6] & bit(i)) != 0; }

    std::int64_t find_next(std::size_t i) const {
        if (i >= n_) return -1;
        std::size_t w = i >> 6;
        std::uint64_t word = words_[w] & (~0ull << (i & 63));
        while (true) {
            if (word) return std::int64_t(w * 64 + std::countr_zero(word));
            if (++w == words_.size()) return -1;
            word = words_[w];
        }
    }

    std::int64_t find_prev(std::size_t i) const {
        if (n_ == 0) return -1;
        if (i >= n_) i = n_ - 1;
        std::size_t w = i >> 6;
        unsigned b = unsigned(i & 63);
        std::uint64_t mask = b == 63 ? ~0ull : ((1ull << (b + 1)) - 1);   // bits 0..b
        std::uint64_t word = words_[w] & mask;
        while (true) {
            if (word) return std::int64_t(w * 64 + 63 - std::countl_zero(word));
            if (w == 0) return -1;
            word = words_[--w];
        }
    }
private:
    static std::uint64_t bit(std::size_t i) { return 1ull << (i & 63); }
    std::size_t n_;
    std::vector<std::uint64_t> words_;
};
```

`countr_zero` and `countl_zero` compile to single `tzcnt` and `lzcnt` instructions, so each loop iteration
inspects 64 levels. A bitmap for 65,536 levels is only 8 KB, small enough to stay in L1. For huge books, add a
second-level summary bitmap with one bit per non-empty word.

## Tests

```cpp
#include <set>
#include <random>
int main() {
    LevelBitmap b(200);
    CHECK_EQ(b.find_next(0), -1);
    CHECK_EQ(b.find_prev(199), -1);
    b.set(5); b.set(64); b.set(130); b.set(199);
    CHECK(b.test(64) && !b.test(63));
    CHECK_EQ(b.find_next(0), 5);
    CHECK_EQ(b.find_next(6), 64);
    CHECK_EQ(b.find_next(65), 130);
    CHECK_EQ(b.find_prev(129), 64);
    CHECK_EQ(b.find_prev(63), 5);
    CHECK_EQ(b.find_prev(4), -1);
    CHECK_EQ(b.find_prev(199), 199);
    CHECK_EQ(b.find_next(200), -1);
    b.clear(64);
    CHECK_EQ(b.find_next(6), 130);
    LevelBitmap r(5000);
    std::set<std::size_t> model;
    std::mt19937 rng(9);
    bool ok = true;
    for (int i = 0; i < 20000; ++i) {
        std::size_t k = rng() % 5000;
        if (rng() % 2) { r.set(k); model.insert(k); } else { r.clear(k); model.erase(k); }
        std::size_t q = rng() % 5000;
        auto it = model.lower_bound(q);
        ok &= r.find_next(q) == (it == model.end() ? -1 : std::int64_t(*it));
        auto jt = model.upper_bound(q);
        ok &= r.find_prev(q) == (jt == model.begin() ? -1 : std::int64_t(*std::prev(jt)));
    }
    CHECK(ok);
    return judge::report();
}
```
