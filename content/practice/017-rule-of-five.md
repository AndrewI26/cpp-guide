+++
title = "Rule of Five: A Price Buffer"
difficulty = "medium"
tags = ["move-semantics", "raii", "memory"]
+++

`PriceBuffer` owns a heap array of doubles. Implement all five special member functions correctly:

```cpp
class PriceBuffer {
public:
    explicit PriceBuffer(std::size_t n);              // n zero-initialized prices
    ~PriceBuffer();
    PriceBuffer(const PriceBuffer& other);            // deep copy
    PriceBuffer& operator=(const PriceBuffer& other); // deep copy, self-assignment safe
    PriceBuffer(PriceBuffer&& other) noexcept;        // steal; leaves other empty (size 0, data nullptr)
    PriceBuffer& operator=(PriceBuffer&& other) noexcept;
    std::size_t size() const;
    double* data();
    double& operator[](std::size_t i);
};
```

The moves must be `noexcept`. `std::vector<PriceBuffer>` only moves elements during reallocation if the move
constructor is `noexcept`; otherwise it copies them. The test checks this.

## Starter Code

```cpp
#pragma once
#include <algorithm>
#include <cstddef>

class PriceBuffer {
public:
    explicit PriceBuffer(std::size_t n) : n_(n), p_(new double[n]()) {}
    ~PriceBuffer() { delete[] p_; }
    // TODO: copy constructor, copy assignment, move constructor, move assignment
    std::size_t size() const { return n_; }
    double* data() { return p_; }
    double& operator[](std::size_t i) { return p_[i]; }
private:
    std::size_t n_;
    double* p_;
};
```

## Hints

- Without user-written copy operations, the compiler-generated copy constructor copies the *pointer*: two
  objects then `delete[]` the same array.
- The **copy-and-swap** idiom gives a self-assignment-safe, exception-safe copy assignment almost for free.
- In the move constructor, null out the source's pointer so its destructor does nothing.

## Solution

```cpp
#pragma once
#include <algorithm>
#include <cstddef>
#include <utility>

class PriceBuffer {
public:
    explicit PriceBuffer(std::size_t n) : n_(n), p_(new double[n]()) {}
    ~PriceBuffer() { delete[] p_; }

    PriceBuffer(const PriceBuffer& o) : n_(o.n_), p_(new double[o.n_]) {
        std::copy(o.p_, o.p_ + o.n_, p_);
    }
    PriceBuffer& operator=(const PriceBuffer& o) {
        if (this != &o) {
            PriceBuffer tmp(o);      // may throw; *this untouched if it does
            swap(tmp);
        }
        return *this;
    }
    PriceBuffer(PriceBuffer&& o) noexcept
        : n_(std::exchange(o.n_, 0)), p_(std::exchange(o.p_, nullptr)) {}
    PriceBuffer& operator=(PriceBuffer&& o) noexcept {
        if (this != &o) {
            delete[] p_;
            n_ = std::exchange(o.n_, 0);
            p_ = std::exchange(o.p_, nullptr);
        }
        return *this;
    }
    void swap(PriceBuffer& o) noexcept { std::swap(n_, o.n_); std::swap(p_, o.p_); }

    std::size_t size() const { return n_; }
    double* data() { return p_; }
    double& operator[](std::size_t i) { return p_[i]; }
private:
    std::size_t n_;
    double* p_;
};
```

Copy is O(n) plus an allocation; move is O(1): two pointer-sized writes. That gap is why move semantics exists.
In real code prefer the **rule of zero**: hold a `std::vector<double>` or `std::unique_ptr<double[]>` and write
none of these by hand.

## Tests

```cpp
#include <type_traits>
#include <vector>
int main() {
    PriceBuffer a(3);
    a[0] = 1; a[1] = 2; a[2] = 3;
    PriceBuffer b(a);
    b[0] = 99;
    CHECK(a[0] == 1 && b[0] == 99 && b.size() == 3 && a.data() != b.data());
    PriceBuffer c(1);
    c = a;
    CHECK(c.size() == 3 && c[2] == 3);
    auto& self = c;
    c = self;
    CHECK(c.size() == 3 && c[1] == 2);
    double* raw = a.data();
    PriceBuffer d(std::move(a));
    CHECK(d.data() == raw && a.size() == 0 && a.data() == nullptr);
    PriceBuffer e(5);
    e = std::move(d);
    CHECK(e.data() == raw && e.size() == 3 && d.data() == nullptr);
    CHECK(std::is_nothrow_move_constructible_v<PriceBuffer>);
    CHECK(std::is_nothrow_move_assignable_v<PriceBuffer>);
    std::vector<PriceBuffer> v;
    for (int i = 0; i < 100; ++i) v.emplace_back(10);
    CHECK(v.size() == 100 && v[57].size() == 10);
    return judge::report();
}
```
