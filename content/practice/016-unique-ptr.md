+++
title = "Write Your Own unique_ptr"
difficulty = "medium"
tags = ["move-semantics", "raii", "templates"]
+++

Implement a minimal `UniquePtr<T>`: sole ownership of a heap object, deleted exactly once, transferable only by
move.

```cpp
template <class T>
class UniquePtr {
public:
    UniquePtr() noexcept;                       // empty
    explicit UniquePtr(T* p) noexcept;          // takes ownership
    ~UniquePtr();
    UniquePtr(UniquePtr&& other) noexcept;
    UniquePtr& operator=(UniquePtr&& other) noexcept;
    UniquePtr(const UniquePtr&) = delete;
    UniquePtr& operator=(const UniquePtr&) = delete;
    T* get() const noexcept;
    T& operator*() const;
    T* operator->() const noexcept;
    explicit operator bool() const noexcept;
    T* release() noexcept;                      // give up ownership without deleting
    void reset(T* p = nullptr) noexcept;        // delete the current object, own p
};
template <class T, class... Args> UniquePtr<T> make_unique_ptr(Args&&... args);
```

`sizeof(UniquePtr<T>)` must equal `sizeof(T*)`. The test counts constructions and destructions, and ASan will
catch any leak or double delete.

## Starter Code

```cpp
#pragma once
#include <utility>

template <class T>
class UniquePtr {
public:
    UniquePtr() noexcept {}
    explicit UniquePtr(T* p) noexcept : p_(p) {}
    ~UniquePtr() {}  // TODO
    UniquePtr(UniquePtr&& other) noexcept {}  // TODO
    UniquePtr& operator=(UniquePtr&& other) noexcept { return *this; }  // TODO
    UniquePtr(const UniquePtr&) = delete;
    UniquePtr& operator=(const UniquePtr&) = delete;
    T* get() const noexcept { return p_; }
    T& operator*() const { return *p_; }
    T* operator->() const noexcept { return p_; }
    explicit operator bool() const noexcept { return p_ != nullptr; }
    T* release() noexcept { return nullptr; }  // TODO
    void reset(T* p = nullptr) noexcept {}  // TODO
private:
    T* p_ = nullptr;
};

template <class T, class... Args>
UniquePtr<T> make_unique_ptr(Args&&... args) { return UniquePtr<T>(); }  // TODO
```

## Hints

- `std::exchange(other.p_, nullptr)` reads the old value and nulls the source in one expression.
- Move assignment must delete what `*this` currently owns, and must be safe for self-assignment (`a = std::move(a)`).
- `make_unique_ptr` should perfectly forward its arguments to `T`'s constructor: `new T(std::forward<Args>(args)...)`.

## Solution

```cpp
#pragma once
#include <utility>

template <class T>
class UniquePtr {
public:
    UniquePtr() noexcept = default;
    explicit UniquePtr(T* p) noexcept : p_(p) {}
    ~UniquePtr() { delete p_; }
    UniquePtr(UniquePtr&& other) noexcept : p_(std::exchange(other.p_, nullptr)) {}
    UniquePtr& operator=(UniquePtr&& other) noexcept {
        reset(other.release());          // safe for self-move: release() then reset() the same pointer
        return *this;
    }
    UniquePtr(const UniquePtr&) = delete;
    UniquePtr& operator=(const UniquePtr&) = delete;
    T* get() const noexcept { return p_; }
    T& operator*() const { return *p_; }
    T* operator->() const noexcept { return p_; }
    explicit operator bool() const noexcept { return p_ != nullptr; }
    T* release() noexcept { return std::exchange(p_, nullptr); }
    void reset(T* p = nullptr) noexcept {
        T* old = std::exchange(p_, p);
        if (old != p) delete old;
    }
private:
    T* p_ = nullptr;
};

template <class T, class... Args>
UniquePtr<T> make_unique_ptr(Args&&... args) {
    return UniquePtr<T>(new T(std::forward<Args>(args)...));
}
```

A `unique_ptr` is a zero-overhead abstraction: one pointer, and with optimizations it compiles to exactly the
code you'd write by hand with `new` and `delete`. The move operations are just pointer swaps. In `reset`,
`exchange` *before* `delete` keeps the object consistent even if `T`'s destructor touches the pointer again.

## Tests

```cpp
struct Tracked {
    static inline int alive = 0;
    int v;
    explicit Tracked(int x) : v(x) { ++alive; }
    ~Tracked() { --alive; }
};
int main() {
    {
        auto a = make_unique_ptr<Tracked>(7);
        CHECK(a && a->v == 7 && (*a).v == 7 && Tracked::alive == 1);
        UniquePtr<Tracked> b = std::move(a);
        CHECK(!a && b && b->v == 7 && Tracked::alive == 1);
        UniquePtr<Tracked> c;
        c = std::move(b);
        CHECK(!b && c->v == 7);
        c = make_unique_ptr<Tracked>(8);           // old object deleted
        CHECK(Tracked::alive == 1 && c->v == 8);
        auto& self = c;
        c = std::move(self);                       // self-move must not delete
        CHECK(c && c->v == 8 && Tracked::alive == 1);
        Tracked* raw = c.release();
        CHECK(!c && Tracked::alive == 1);
        c.reset(raw);
        c.reset();
        CHECK(!c && Tracked::alive == 0);
        c.reset(new Tracked(9));
    }
    CHECK_EQ(Tracked::alive, 0);
    CHECK_EQ(sizeof(UniquePtr<Tracked>), sizeof(Tracked*));
    return judge::report();
}
```
