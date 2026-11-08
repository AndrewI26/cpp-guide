+++
title = "Small Vector with Inline Storage"
difficulty = "hard"
tags = ["memory", "templates", "move-semantics"]
+++

Most price levels hold only a few orders, but `std::vector` heap-allocates as soon as you add the first element.
A **small vector** stores up to `N` elements inline (inside the object itself) and only moves to the heap when
it grows beyond that.

```cpp
template <class T, std::size_t N>
class SmallVector {
public:
    SmallVector() noexcept;
    ~SmallVector();
    SmallVector(const SmallVector&);
    SmallVector(SmallVector&&) noexcept;
    SmallVector& operator=(SmallVector other) noexcept;   // copy-and-swap style: by value
    void push_back(const T& v);
    void push_back(T&& v);
    void pop_back();
    T& operator[](std::size_t i);
    const T& operator[](std::size_t i) const;
    std::size_t size() const;
    std::size_t capacity() const;
    bool is_inline() const;                // true while elements live in the inline buffer
    T* begin(); T* end();
};
```

Growth doubles the capacity. Elements must be properly constructed and destroyed: the tests use a type that
counts its live instances, and ASan checks for leaks.

## Starter Code

```cpp
#pragma once
#include <cstddef>
#include <memory>
#include <new>
#include <utility>

template <class T, std::size_t N>
class SmallVector {
public:
    SmallVector() noexcept {}
    ~SmallVector() {}
    SmallVector(const SmallVector&) {}
    SmallVector(SmallVector&&) noexcept {}
    SmallVector& operator=(SmallVector other) noexcept { return *this; }
    void push_back(const T& v) {}
    void push_back(T&& v) {}
    void pop_back() {}
    T& operator[](std::size_t i) { return data()[i]; }
    const T& operator[](std::size_t i) const { return data()[i]; }
    std::size_t size() const { return 0; }
    std::size_t capacity() const { return N; }
    bool is_inline() const { return true; }
    T* begin() { return data(); }
    T* end() { return data(); }
private:
    T* data() { return reinterpret_cast<T*>(buf_); }
    const T* data() const { return reinterpret_cast<const T*>(buf_); }
    alignas(T) unsigned char buf_[sizeof(T) * N];
};
```

## Hints

- Keep `T* data_` pointing either at the inline buffer or at heap memory, plus `size_` and `cap_`. `is_inline()`
  is `data_ == inline_ptr()`.
- Grow: allocate raw memory with `::operator new(bytes, std::align_val_t{alignof(T)})`, move-construct the
  elements into it (`std::uninitialized_move`), destroy the old ones, and free the old block if it was on the heap.
- Moving a heap-backed vector steals the pointer. Moving an inline one must move each element, because the
  buffer itself can't be stolen.
- `push_back(const T& v)` where `v` refers to an element of *this* vector, and the push triggers a regrowth: copy
  `v` before reallocating.

## Solution

```cpp
#pragma once
#include <cstddef>
#include <memory>
#include <new>
#include <utility>

template <class T, std::size_t N>
class SmallVector {
public:
    SmallVector() noexcept = default;
    ~SmallVector() { clear(); release(); }

    SmallVector(const SmallVector& o) {
        reserve(o.size_);
        std::uninitialized_copy(o.data_, o.data_ + o.size_, data_);
        size_ = o.size_;
    }
    SmallVector(SmallVector&& o) noexcept { steal(std::move(o)); }
    SmallVector& operator=(SmallVector o) noexcept {
        clear(); release();
        data_ = inline_ptr(); cap_ = N;
        steal(std::move(o));
        return *this;
    }

    void push_back(const T& v) { emplace(v); }
    void push_back(T&& v) { emplace(std::move(v)); }
    void pop_back() { std::destroy_at(data_ + --size_); }

    T& operator[](std::size_t i) { return data_[i]; }
    const T& operator[](std::size_t i) const { return data_[i]; }
    std::size_t size() const { return size_; }
    std::size_t capacity() const { return cap_; }
    bool is_inline() const { return data_ == inline_ptr(); }
    T* begin() { return data_; }
    T* end() { return data_ + size_; }

private:
    template <class U>
    void emplace(U&& v) {
        if (size_ == cap_) {
            T tmp(std::forward<U>(v));          // v might alias an element we're about to move
            reserve(cap_ * 2);
            std::construct_at(data_ + size_, std::move(tmp));
        } else {
            std::construct_at(data_ + size_, std::forward<U>(v));
        }
        ++size_;
    }
    void reserve(std::size_t want) {
        if (want <= cap_) return;
        T* fresh = static_cast<T*>(::operator new(want * sizeof(T), std::align_val_t{alignof(T)}));
        std::uninitialized_move(data_, data_ + size_, fresh);
        std::destroy(data_, data_ + size_);
        release();
        data_ = fresh;
        cap_ = want;
    }
    void steal(SmallVector&& o) noexcept {
        if (o.is_inline()) {
            std::uninitialized_move(o.data_, o.data_ + o.size_, data_);
            size_ = o.size_;
            o.clear();
        } else {
            data_ = std::exchange(o.data_, o.inline_ptr());
            size_ = std::exchange(o.size_, 0);
            cap_ = std::exchange(o.cap_, N);
        }
    }
    void clear() { std::destroy(data_, data_ + size_); size_ = 0; }
    void release() {
        if (!is_inline()) ::operator delete(data_, std::align_val_t{alignof(T)});
    }
    T* inline_ptr() { return std::launder(reinterpret_cast<T*>(buf_)); }
    const T* inline_ptr() const { return std::launder(reinterpret_cast<const T*>(buf_)); }

    alignas(T) unsigned char buf_[sizeof(T) * N];
    T* data_ = inline_ptr();
    std::size_t size_ = 0, cap_ = N;
};
```

The core idea is that `data_` points at one of two places, and every operation must respect which one. The
subtle parts are moving from an inline vector (the elements must be moved one by one, since the buffer can't be
stolen) and the self-referencing `push_back` (copy first, then grow). LLVM's `SmallVector`,
`boost::container::small_vector` and folly's `small_vector` use the same design. (Strictly speaking, the
`noexcept` move assumes `T`'s move constructor doesn't throw.)

## Tests

```cpp
#include <string>
struct Counted {
    static inline int live = 0;
    std::string s;
    Counted(std::string v) : s(std::move(v)) { ++live; }
    Counted(const Counted& o) : s(o.s) { ++live; }
    Counted(Counted&& o) noexcept : s(std::move(o.s)) { ++live; }
    ~Counted() { --live; }
};
int main() {
    {
        SmallVector<int, 4> v;
        for (int i = 0; i < 4; ++i) v.push_back(i);
        CHECK(v.is_inline() && v.size() == 4 && v.capacity() == 4);
        v.push_back(4);
        CHECK(!v.is_inline() && v.size() == 5 && v.capacity() == 8);
        bool ok = true;
        for (int i = 0; i < 5; ++i) ok &= v[i] == i;
        CHECK(ok);
        v.push_back(v[0]);                        // aliasing push across a regrowth boundary is fine
        CHECK(v[5] == 0);
        v.pop_back();
        CHECK(v.size() == 5);
        int sum = 0;
        for (int x : v) sum += x;
        CHECK_EQ(sum, 10);
    }
    {
        SmallVector<Counted, 2> a;
        a.push_back(Counted("x"));
        a.push_back(Counted("y"));
        CHECK(a.is_inline() && Counted::live == 2);
        SmallVector<Counted, 2> b = a;           // copy (inline)
        CHECK(b.size() == 2 && b[1].s == "y" && Counted::live == 4);
        a.push_back(Counted("z"));                // goes to the heap
        CHECK(!a.is_inline() && Counted::live == 5);
        a.push_back(a[0]);                        // alias + grow
        CHECK(a[3].s == "x");
        SmallVector<Counted, 2> c = std::move(a); // steal the heap buffer
        CHECK(c.size() == 4 && a.size() == 0 && a.is_inline() && !c.is_inline());
        SmallVector<Counted, 2> d = std::move(b); // inline move: element-wise
        CHECK(d.size() == 2 && d[0].s == "x" && b.size() == 0);
        d = c;                                    // copy-assign heap into inline
        CHECK(d.size() == 4 && d[2].s == "z");
        c = SmallVector<Counted, 2>();
        CHECK(c.size() == 0);
    }
    CHECK_EQ(Counted::live, 0);
    return judge::report();
}
```
