+++
title = "Fixed-Size Object Pool"
difficulty = "hard"
tags = ["memory", "allocators", "templates"]
+++

Order objects are created and destroyed constantly. `new`/`delete` costs tens to hundreds of nanoseconds each,
with unpredictable tail latency. Implement a pool that pre-allocates storage for `N` objects and recycles slots
through an intrusive **free list**:

```cpp
template <class T, std::size_t N>
class ObjectPool {
public:
    ObjectPool();
    ~ObjectPool();                                     // destroys any objects still alive
    template <class... Args> T* create(Args&&... args);   // nullptr if the pool is full
    void destroy(T* p);                                // runs ~T, returns the slot to the pool
    std::size_t live() const;                          // objects currently alive
    static constexpr std::size_t capacity() { return N; }
};
```

Requirements: no heap allocation after construction (the storage may live inside the pool object itself), O(1)
`create` and `destroy`, and objects properly aligned for `T`. The destructor must destroy objects that are still
alive. The tests count constructions and destructions.

## Starter Code

```cpp
#pragma once
#include <cstddef>
#include <new>
#include <utility>

template <class T, std::size_t N>
class ObjectPool {
public:
    ObjectPool() {}
    ~ObjectPool() {}
    template <class... Args> T* create(Args&&... args) { return nullptr; }
    void destroy(T* p) {}
    std::size_t live() const { return 0; }
    static constexpr std::size_t capacity() { return N; }
};
```

## Hints

- Each slot is a `union { T value; Slot* next; }`, or raw aligned bytes big enough for either. A free slot stores
  the pointer to the next free slot inside itself, so the free list needs no extra memory.
- To destroy live objects in the destructor, you need to know which slots are live. Keep a small `bool`
  array (or bitset) alongside the slots.
- Use `std::construct_at` / placement new, and `std::destroy_at` / `p->~T()`.

## Solution

```cpp
#pragma once
#include <array>
#include <cstddef>
#include <memory>
#include <new>
#include <utility>

template <class T, std::size_t N>
class ObjectPool {
    union Slot {
        Slot* next;
        alignas(T) unsigned char storage[sizeof(T)];
    };
public:
    ObjectPool() {
        for (std::size_t i = 0; i + 1 < N; ++i) slots_[i].next = &slots_[i + 1];
        slots_[N - 1].next = nullptr;
        free_ = &slots_[0];
    }
    ~ObjectPool() {
        for (std::size_t i = 0; i < N; ++i)
            if (alive_[i]) std::destroy_at(ptr(i));
    }
    ObjectPool(const ObjectPool&) = delete;
    ObjectPool& operator=(const ObjectPool&) = delete;

    template <class... Args>
    T* create(Args&&... args) {
        if (!free_) return nullptr;
        Slot* s = free_;
        Slot* next = s->next;          // read the link BEFORE constructing T over these bytes
        T* p = ::new (static_cast<void*>(s->storage)) T(std::forward<Args>(args)...);   // if this throws, the pool is unchanged
        free_ = next;
        alive_[static_cast<std::size_t>(s - slots_.data())] = true;
        ++live_;
        return p;
    }

    void destroy(T* p) {
        Slot* s = reinterpret_cast<Slot*>(p);
        std::size_t i = static_cast<std::size_t>(s - slots_.data());
        std::destroy_at(p);
        alive_[i] = false;
        --live_;
        s->next = free_;
        free_ = s;
    }

    std::size_t live() const { return live_; }
    static constexpr std::size_t capacity() { return N; }
private:
    T* ptr(std::size_t i) { return std::launder(reinterpret_cast<T*>(slots_[i].storage)); }
    std::array<Slot, N> slots_;
    std::array<bool, N> alive_{};
    Slot* free_ = nullptr;
    std::size_t live_ = 0;
};
```

The one trap is the order of operations in `create`. A free slot stores the `next` link *in the same bytes* that
`T` will occupy, so constructing the object overwrites the link. Read `next` first, construct, and only then
advance `free_`. Advancing `free_` after construction also keeps the pool unchanged if `T`'s constructor throws.

`create` and `destroy` are a handful of instructions each: pop or push the head of a singly linked list. Recently
freed slots are reused first (LIFO), so they're likely still hot in cache.

## Tests

```cpp
#include <cstdint>
#include <vector>
struct Order {
    static inline int ctor = 0, dtor = 0;
    long id; double px;
    Order(long i, double p) : id(i), px(p) { ++ctor; }
    ~Order() { ++dtor; }
};
int main() {
    {
        ObjectPool<Order, 4> pool;
        Order* a = pool.create(1, 100.0);
        Order* b = pool.create(2, 101.0);
        CHECK(a && b && a != b && a->id == 1 && b->px == 101.0);
        CHECK(reinterpret_cast<std::uintptr_t>(a) % alignof(Order) == 0);
        CHECK_EQ(pool.live(), 2u);
        Order* c = pool.create(3, 1.0);
        Order* d = pool.create(4, 1.0);
        CHECK(c && d && !pool.create(5, 1.0));
        pool.destroy(b);
        CHECK_EQ(pool.live(), 3u);
        Order* e = pool.create(6, 6.0);
        CHECK(e == b && e->id == 6);          // slot reused
        CHECK(a->id == 1 && c->id == 3 && d->id == 4);
        pool.destroy(a);
        pool.destroy(c);
    }   // d and e are destroyed by ~ObjectPool
    CHECK_EQ(Order::ctor, 5);
    CHECK_EQ(Order::dtor, 5);
    ObjectPool<std::vector<int>, 64> vp;
    std::vector<std::vector<int>*> ptrs;
    bool ok = true;
    for (int round = 0; round < 1000; ++round) {
        while (auto* p = vp.create(10, round)) ptrs.push_back(p);
        ok &= ptrs.size() == 64;
        for (auto* p : ptrs) { ok &= (*p)[9] == round; vp.destroy(p); }
        ptrs.clear();
    }
    CHECK(ok && vp.live() == 0);
    return judge::report();
}
```
