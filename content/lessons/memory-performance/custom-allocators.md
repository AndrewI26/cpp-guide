+++
title = "Arenas, Pools and PMR"
minutes = 45
practice_tags = ["allocators", "memory", "templates"]
+++

## Why this chapter matters

Chapter 7.4 established the rule: no general-purpose allocation on the hot path. But an order book still needs to
create an order node when an add arrives and destroy it when the order is cancelled, and a parser still needs
scratch space for each message. The answer is not to avoid allocation altogether but to use **allocators designed
for the workload**: ones that know every object is the same size, or that everything is freed at once, and so can
allocate in a few instructions with no locks, no searching and no surprises.

The two workhorses are the **arena** (bump allocator) and the **pool** (free-list allocator). Both fit in a page of
code, and C++17's `std::pmr` lets standard containers use them without changing their types much. Measured on the
test machine, a `std::list` with nodes from an arena builds and tears down 4× faster than with the default
allocator, and allocation becomes deterministic.

By the end of this chapter you should be able to:

- implement a bump (arena) allocator and explain when it applies;
- implement a fixed-size object pool with an intrusive free list;
- use `std::pmr` memory resources with standard containers;
- write a minimal standard-conforming allocator, and know its pitfalls.

---

## 1. Arenas: bump allocation

An **arena** (also called a bump allocator, linear allocator or region) owns one large block of memory and a pointer
to the next free byte. Allocating means aligning the pointer and advancing it. There's no per-object free; the whole
arena is reset at once.

```cpp
class Arena {
public:
    Arena(std::byte* buf, std::size_t size) : begin_(buf), cur_(buf), end_(buf + size) {}

    void* allocate(std::size_t n, std::size_t align = alignof(std::max_align_t)) {
        auto p = reinterpret_cast<std::uintptr_t>(cur_);
        auto aligned = (p + align - 1) & ~(std::uintptr_t{align} - 1);   // align must be a power of two
        if (aligned + n > reinterpret_cast<std::uintptr_t>(end_)) return nullptr;   // out of space
        cur_ = reinterpret_cast<std::byte*>(aligned + n);
        return reinterpret_cast<void*>(aligned);
    }

    template <class T, class... Args>
    T* create(Args&&... args) {
        void* p = allocate(sizeof(T), alignof(T));
        return p ? new (p) T(std::forward<Args>(args)...) : nullptr;   // placement new
    }

    void reset() { cur_ = begin_; }        // frees everything at once (doesn't run destructors)
    std::size_t used() const { return static_cast<std::size_t>(cur_ - begin_); }

private:
    std::byte* begin_;
    std::byte* cur_;
    std::byte* end_;
};
```

Allocation is an add, a mask, a compare and a store: about a nanosecond. Objects allocated consecutively are
adjacent in memory, so they're cache-friendly. And there's no fragmentation.

The catch is the lifetime model: **everything in an arena dies together**. That fits beautifully when work comes in
units:

- **Per message**: all temporaries created while parsing and handling one message go in an arena that's reset after
  the message.
- **Per request or per batch** in a backtest or risk calculation.
- **Per session**: reference data loaded at start-up and never freed until shutdown.

```cpp
alignas(64) std::byte scratch[64 * 1024];
Arena arena(scratch, sizeof scratch);

void on_message(std::span<const std::byte> msg) {
    arena.reset();                                     // reclaim everything from the previous message
    auto* fields = arena.create<FieldTable>();
    parse_into(msg, *fields, arena);                   // the parser allocates freely from the arena
    handle(*fields);
}
```

> [!WARNING]
> `reset()` doesn't call destructors. That's fine for trivially destructible types (check with
> `std::is_trivially_destructible_v`), but objects that own resources (a `std::string` holding heap memory, a file)
> would leak. Either keep arena contents trivially destructible, or record destructors to run on reset.

---

## 2. Pools: fixed-size free lists

When objects have individual, unpredictable lifetimes (orders that are added and cancelled in any order) but are all
the **same size**, use a **pool**. Preallocate an array of slots and keep the free ones in a linked list threaded
through the slots themselves (an **intrusive free list**):

```cpp
template <class T>
class Pool {
    union Slot {
        Slot* next;                         // when free: link to the next free slot
        alignas(T) std::byte storage[sizeof(T)];   // when in use: holds a T
    };
public:
    explicit Pool(std::size_t capacity) : slots_(capacity) {
        for (std::size_t i = 0; i + 1 < capacity; ++i) slots_[i].next = &slots_[i + 1];
        if (capacity) slots_[capacity - 1].next = nullptr;
        free_ = capacity ? &slots_[0] : nullptr;
    }

    template <class... Args>
    T* create(Args&&... args) {
        if (!free_) return nullptr;                       // exhausted: a policy decision, not a malloc
        Slot* s = free_;
        free_ = s->next;
        return new (s->storage) T(std::forward<Args>(args)...);
    }

    void destroy(T* p) noexcept {
        p->~T();
        Slot* s = reinterpret_cast<Slot*>(p);
        s->next = free_;
        free_ = s;
    }

private:
    std::vector<Slot> slots_;     // one contiguous block, allocated (and ideally pre-faulted) at start-up
    Slot* free_ = nullptr;
};
```

`create` pops the head of the free list and `destroy` pushes onto it: a couple of loads and stores, always, with no
locking (the pool is used by one thread) and no searching. Because the free list is LIFO, the most recently freed
slot, still hot in cache, is reused first.

Pools are standard in trading systems for orders, price levels, and messages in flight. The practice set's "Fixed-Size
Object Pool" problem builds a full version.

**Indices instead of pointers.** A variation stores a `std::uint32_t` index of the next free slot instead of a
pointer, and hands out indices instead of `T*`. Indices are half the size, remain valid if the pool's storage is
relocated, and work in shared memory (Chapter 7.1).

### Exercises

:::exercise Pool or arena?
Choose a pool, an arena or neither for each: (a) order objects in a matching engine; (b) the decoded fields of each
incoming FIX message, used only while handling it; (c) strategy configuration loaded at start-up; (d) a cache of
recently seen instruments with an LRU eviction policy, entries of identical size.
:::solution
(a) Pool: same size, independent lifetimes. (b) Arena, reset per message. (c) Arena (or ordinary allocation, since
it's at start-up and never freed; an arena keeps it compact and contiguous). (d) Pool: fixed-size entries, freed
individually on eviction.
:::

---

## 3. `std::pmr`: polymorphic memory resources

Standard containers take an allocator as a template parameter, so `std::vector<int, MyAlloc>` is a different type
from `std::vector<int>`, which spreads through every interface. C++17's **polymorphic memory resources** fix this:
`std::pmr::vector<T>` (an alias for `std::vector<T, std::pmr::polymorphic_allocator<T>>`) holds a pointer to a
`std::pmr::memory_resource`, and the resource is chosen at run time.

The standard resources:

| Resource | Behaviour |
|---|---|
| `std::pmr::monotonic_buffer_resource` | an arena: bump allocation from a buffer, frees nothing until destroyed or `release()`d |
| `std::pmr::unsynchronized_pool_resource` | pools of fixed-size blocks per size class, single-threaded |
| `std::pmr::synchronized_pool_resource` | the same, thread-safe |
| `std::pmr::new_delete_resource()` | forwards to `operator new`/`delete` |
| `std::pmr::null_memory_resource()` | throws `std::bad_alloc` on any allocation |

Resources can be chained: a monotonic buffer that runs out of space asks its **upstream** resource for more. Making
the upstream `null_memory_resource()` turns "the arena ran out" into a loud failure instead of a silent fallback to
`malloc`, which is exactly what you want on a hot path.

```cpp
alignas(64) std::byte buffer[1 << 20];                         // preallocated (and pre-faulted) at start-up

void handle_batch(std::span<const Msg> msgs) {
    std::pmr::monotonic_buffer_resource arena(buffer, sizeof buffer, std::pmr::null_memory_resource());

    std::pmr::vector<Order> orders(&arena);                    // all of these allocate from the buffer
    std::pmr::unordered_map<std::uint64_t, std::size_t> by_id(&arena);
    std::pmr::string note("allocated in the arena, not on the heap", &arena);

    for (const Msg& m : msgs) { /* ... */ }
}   // arena destroyed: everything released at once, no individual frees
```

**Nested containers propagate the resource automatically**: a `std::pmr::vector<std::pmr::string>` gives each string
the vector's resource, because `polymorphic_allocator` supports uses-allocator construction.

The measurement from Chapter 7.4: building and destroying a `std::list` of one million `int64_t`s cost 42.8 ns per
node with the default allocator, 10.2 ns per node as a `std::pmr::list` on a `monotonic_buffer_resource`, and 9.8 ns
with the resource over a pre-faulted buffer.

> [!PERF]
> The costs of `pmr`: each allocation is a virtual call to the resource (usually well predicted, since one resource
> is used), and each container carries an extra pointer. For node-based containers, which allocate on every insert,
> the gain dwarfs these costs. Also beware that a `monotonic_buffer_resource` never reuses freed memory, so a
> long-lived container with constant inserts and erases will exhaust it; use a pool resource for that pattern.

### Exercises

:::exercise Make it fail loudly
Why give a hot-path `monotonic_buffer_resource` an upstream of `null_memory_resource()` instead of the default
(`new_delete_resource`)? What's the risk?
:::solution
With the default upstream, an arena that runs out of space silently falls back to `operator new`, so the "allocation
free" hot path starts calling `malloc` exactly when load is highest (when messages are biggest or most numerous),
which is the worst time for a latency spike, and nobody notices until latency graphs show it. With
`null_memory_resource`, exhaustion throws `std::bad_alloc` immediately, in testing, so you can size the buffer
correctly. The risk is that it throws in production if the buffer is undersized; you must handle that (reject the
message, fail over), but that's a conscious, visible decision.
:::

---

## 4. Writing a standard allocator

Before `pmr`, the only way to give a container a custom allocator was to write an allocator type. It's still useful
when you want zero overhead (no virtual call) and are happy with the type change. The minimal interface since C++11
is small:

```cpp
template <class T>
struct ArenaAllocator {
    using value_type = T;

    Arena* arena;

    explicit ArenaAllocator(Arena& a) noexcept : arena(&a) {}
    template <class U> ArenaAllocator(const ArenaAllocator<U>& o) noexcept : arena(o.arena) {}   // rebinding

    T* allocate(std::size_t n) {
        void* p = arena->allocate(n * sizeof(T), alignof(T));
        if (!p) throw std::bad_alloc{};
        return static_cast<T*>(p);
    }
    void deallocate(T*, std::size_t) noexcept {}              // arena: individual frees are no-ops

    template <class U> bool operator==(const ArenaAllocator<U>& o) const noexcept { return arena == o.arena; }
};

Arena arena(buf, sizeof buf);
std::vector<int, ArenaAllocator<int>> v(ArenaAllocator<int>{arena});
```

`std::allocator_traits` fills in everything else. The converting constructor matters because node-based containers
**rebind** the allocator: a `std::list<int, A<int>>` actually allocates `A<ListNode<int>>`.

Pitfalls: allocators with state (like the `arena` pointer) must compare equal only when memory from one can be freed
by the other, and the rules for what happens to the allocator when a container is copied, moved or swapped
(`propagate_on_container_*`) are subtle. `std::pmr` exists largely so that you don't have to think about them.

---

## Summary

- An **arena** allocates by bumping a pointer and frees everything at once. Use it for objects with a shared lifetime:
  per message, per batch, per session. `reset()` doesn't run destructors.
- A **pool** recycles fixed-size slots through an intrusive free list: O(1), lock-free for one thread, LIFO reuse of
  hot slots. Use it for orders, levels and messages.
- `std::pmr` lets standard containers use arenas and pools chosen at run time, with nested containers inheriting the
  resource. Chain to `null_memory_resource()` to make exhaustion loud.
- A `std::list` on a `pmr` arena was 4× faster than with the default allocator on the test machine.
- Custom allocator types avoid `pmr`'s virtual call but change container types and have subtle propagation rules.

## Key terms

**Arena / bump allocator** · **reset** · **placement new** · **pool allocator** · **intrusive free list** ·
**`std::pmr`** · **`memory_resource`** · **`monotonic_buffer_resource`** · **`unsynchronized_pool_resource`** ·
**upstream resource** · **`polymorphic_allocator`** · **allocator rebinding**
