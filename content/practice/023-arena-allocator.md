+++
title = "Bump Arena Allocator"
difficulty = "medium"
tags = ["memory", "allocators"]
+++

`malloc` is general-purpose, and therefore slow and unpredictable. Many hot paths allocate lots of short-lived
objects that all die together (say, everything built while processing one message). An **arena** hands out
memory by bumping a pointer through a pre-allocated block, and frees everything at once with `reset()`.

```cpp
class Arena {
public:
    explicit Arena(std::size_t capacity_bytes);
    void* allocate(std::size_t size, std::size_t align = alignof(std::max_align_t));  // nullptr if out of space
    template <class T, class... Args> T* make(Args&&... args);  // allocate + construct; nullptr if no space
    void reset();                      // free everything (does NOT run destructors)
    std::size_t used() const;          // bytes consumed, including alignment padding
    std::size_t capacity() const;
};
```

`align` is a power of two. Returned pointers must satisfy it.

## Starter Code

```cpp
#pragma once
#include <cstddef>
#include <cstdint>
#include <memory>
#include <new>
#include <utility>

class Arena {
public:
    explicit Arena(std::size_t capacity_bytes)
        : buf_(new std::byte[capacity_bytes]), cap_(capacity_bytes) {}
    void* allocate(std::size_t size, std::size_t align = alignof(std::max_align_t)) { return nullptr; }
    template <class T, class... Args> T* make(Args&&... args) { return nullptr; }
    void reset() {}
    std::size_t used() const { return 0; }
    std::size_t capacity() const { return cap_; }
private:
    std::unique_ptr<std::byte[]> buf_;
    std::size_t cap_;
    std::size_t off_ = 0;
};
```

## Hints

- Round the current *address* (not just the offset) up to the alignment:
  `aligned = (addr + align - 1) & ~(align - 1)`.
- Check for space *before* moving the offset.
- `make` uses placement new: `::new (p) T(std::forward<Args>(args)...)`.

## Solution

```cpp
#pragma once
#include <cstddef>
#include <cstdint>
#include <memory>
#include <new>
#include <utility>

class Arena {
public:
    explicit Arena(std::size_t capacity_bytes)
        : buf_(new std::byte[capacity_bytes]), cap_(capacity_bytes) {}

    void* allocate(std::size_t size, std::size_t align = alignof(std::max_align_t)) {
        auto base = reinterpret_cast<std::uintptr_t>(buf_.get());
        std::uintptr_t cur = base + off_;
        std::uintptr_t aligned = (cur + align - 1) & ~(std::uintptr_t(align) - 1);
        std::size_t new_off = (aligned - base) + size;
        if (new_off > cap_) return nullptr;
        off_ = new_off;
        return reinterpret_cast<void*>(aligned);
    }

    template <class T, class... Args>
    T* make(Args&&... args) {
        void* p = allocate(sizeof(T), alignof(T));
        return p ? ::new (p) T(std::forward<Args>(args)...) : nullptr;
    }

    void reset() { off_ = 0; }
    std::size_t used() const { return off_; }
    std::size_t capacity() const { return cap_; }
private:
    std::unique_ptr<std::byte[]> buf_;
    std::size_t cap_;
    std::size_t off_ = 0;
};
```

An allocation is a few arithmetic instructions and a compare, with no locks, no free lists and no system calls,
and consecutive allocations are adjacent in memory, which is ideal for cache locality. The trade-off: you can't
free individual objects, and destructors don't run. Use it for trivially destructible data, or call destructors
yourself. `std::pmr::monotonic_buffer_resource` is the standard library's version.

## Tests

```cpp
#include <cstring>
struct Pt { double x, y; Pt(double a, double b) : x(a), y(b) {} };
int main() {
    Arena a(1024);
    void* p1 = a.allocate(1, 1);
    void* p2 = a.allocate(8, 8);
    CHECK(p1 != nullptr && p2 != nullptr);
    CHECK(reinterpret_cast<std::uintptr_t>(p2) % 8 == 0);
    CHECK(static_cast<std::byte*>(p2) > static_cast<std::byte*>(p1));
    void* p3 = a.allocate(16, 64);
    CHECK(reinterpret_cast<std::uintptr_t>(p3) % 64 == 0);
    std::memset(p3, 0xAB, 16);
    Pt* pt = a.make<Pt>(1.5, 2.5);
    CHECK(pt && pt->x == 1.5 && pt->y == 2.5);
    CHECK(reinterpret_cast<std::uintptr_t>(pt) % alignof(Pt) == 0);
    CHECK(a.used() <= a.capacity());
    CHECK(a.allocate(2000) == nullptr);
    std::size_t before = a.used();
    CHECK(a.allocate(2000) == nullptr && a.used() == before);   // failure doesn't consume space
    a.reset();
    CHECK_EQ(a.used(), 0u);
    int count = 0;
    while (a.make<std::uint64_t>(std::uint64_t(count))) ++count;
    CHECK(count >= 120 && count <= 128);
    return judge::report();
}
```
