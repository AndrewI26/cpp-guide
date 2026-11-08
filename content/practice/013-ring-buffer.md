+++
title = "Fixed-Capacity Ring Buffer"
difficulty = "medium"
tags = ["data-structures", "templates"]
+++

Implement a single-threaded, fixed-capacity FIFO with storage inline (no heap):

```cpp
template <class T, std::size_t N>
class RingBuffer {
public:
    bool push(const T& v);          // false if full
    bool pop(T& out);               // false if empty
    T& front();                     // precondition: !empty()
    T& operator[](std::size_t i);   // i-th element from the front, precondition i < size()
    std::size_t size() const;
    bool empty() const;
    bool full() const;
    static constexpr std::size_t capacity() { return N; }
};
```

It must hold exactly `N` elements (no wasted slot), and `sizeof(RingBuffer<T, N>)` must be at most
`N * sizeof(T) + 2 * sizeof(std::size_t)`.

## Starter Code

```cpp
#pragma once
#include <array>
#include <cstddef>

template <class T, std::size_t N>
class RingBuffer {
public:
    bool push(const T& v) { return false; }
    bool pop(T& out) { return false; }
    T& front() { return data_[0]; }
    T& operator[](std::size_t i) { return data_[i]; }
    std::size_t size() const { return 0; }
    bool empty() const { return true; }
    bool full() const { return false; }
    static constexpr std::size_t capacity() { return N; }
private:
    std::array<T, N> data_{};
};
```

## Hints

- Store `head` (index of the front) and `count`. The back is `(head + count) % N`.
- If you track `head` and `tail` instead, you need a wasted slot or an extra flag to tell "full" from "empty".

## Solution

```cpp
#pragma once
#include <array>
#include <cstddef>

template <class T, std::size_t N>
class RingBuffer {
    static_assert(N > 0);
public:
    bool push(const T& v) {
        if (count_ == N) return false;
        data_[wrap(head_ + count_)] = v;
        ++count_;
        return true;
    }
    bool pop(T& out) {
        if (count_ == 0) return false;
        out = std::move(data_[head_]);
        head_ = wrap(head_ + 1);
        --count_;
        return true;
    }
    T& front() { return data_[head_]; }
    T& operator[](std::size_t i) { return data_[wrap(head_ + i)]; }
    std::size_t size() const { return count_; }
    bool empty() const { return count_ == 0; }
    bool full() const { return count_ == N; }
    static constexpr std::size_t capacity() { return N; }
private:
    static constexpr std::size_t wrap(std::size_t i) { return i >= N ? i - N : i; }   // i < 2N always
    std::array<T, N> data_{};
    std::size_t head_ = 0, count_ = 0;
};
```

`head + count` is always below `2N`, so one conditional subtraction replaces `%`. If `N` is a power of two,
the compiler would turn `% N` into an AND anyway, but this version is fast for any `N`.

## Tests

```cpp
#include <string>
int main() {
    RingBuffer<int, 3> r;
    CHECK(r.empty() && !r.full() && r.capacity() == 3);
    CHECK(r.push(1) && r.push(2) && r.push(3) && !r.push(4));
    CHECK(r.full() && r.size() == 3);
    CHECK_EQ(r.front(), 1);
    CHECK_EQ(r[2], 3);
    int v;
    CHECK(r.pop(v) && v == 1);
    CHECK(r.push(4));
    CHECK(r[0] == 2 && r[1] == 3 && r[2] == 4);
    for (int want : {2, 3, 4}) CHECK(r.pop(v) && v == want);
    CHECK(!r.pop(v));
    RingBuffer<std::string, 5> s;
    bool ok = true;
    for (int i = 0; i < 1000; ++i) { s.push(std::to_string(i)); std::string out; ok &= s.pop(out) && out == std::to_string(i); }
    CHECK(ok);
    CHECK(sizeof(RingBuffer<double, 16>) <= 16 * sizeof(double) + 2 * sizeof(std::size_t));
    return judge::report();
}
```
