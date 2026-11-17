+++
title = "Smart Pointers and Ownership"
minutes = 40
practice_tags = ["raii", "move-semantics", "memory"]
+++

## Why this chapter matters

A market-data distribution layer passes each update to subscribers as a `std::shared_ptr<const Update>`, so that
"nobody has to think about lifetimes." Under load, profiling shows a surprising hotspot: the atomic increments and
decrements of reference counts. Every copy of the `shared_ptr`, into each subscriber's queue and into each
function that takes it by value, is a locked instruction on a cache line shared across cores. With eight consumer
threads, that one cache line bounces between cores millions of times per second (Chapter 10.3).

Smart pointers express ownership in the type system (Chapter 1.4), and they make memory leaks and double frees
nearly impossible. But the two main ones have very different costs. `std::unique_ptr` is free: it compiles to
exactly the code you'd write by hand. `std::shared_ptr` is not: it has a separate control block, atomic reference
counting, and twice the size. Choosing well is mostly a matter of thinking clearly about who owns what.

By the end of this chapter you should be able to:

- use `std::unique_ptr` for exclusive ownership, including with custom deleters;
- describe how `std::shared_ptr` works: control blocks, atomic counts, `make_shared`, `weak_ptr`;
- pass smart pointers to functions correctly;
- recognize when neither smart pointer is the right answer.

---

## 1. `std::unique_ptr`: exclusive ownership

A `std::unique_ptr<T>` owns one heap object and deletes it in its destructor. It's move-only: ownership can be
transferred but never duplicated.

```cpp
auto book = std::make_unique<OrderBook>(instrument);   // allocates and constructs
book->add(order);
std::unique_ptr<OrderBook> other = std::move(book);    // ownership transferred; book is now null
// other's destructor deletes the OrderBook
```

**Cost: none.** A `unique_ptr<T>` with the default deleter is exactly one pointer, 8 bytes. Dereferencing it is
dereferencing a pointer. Its destructor is `if (p) delete p;`, inlined. Moving it copies a pointer and nulls the
source. There's no reference count and no hidden allocation. Use it freely.

```cpp
static_assert(sizeof(std::unique_ptr<OrderBook>) == sizeof(OrderBook*));
```

### When to use it

- **Polymorphic objects**: `std::vector<std::unique_ptr<Strategy>>`. A container of base pointers that owns its
  elements.
- **Large objects** that shouldn't live on the stack, or whose size isn't known until run time.
- **Pimpl**: hiding a class's implementation behind a pointer to an incomplete type, to cut compile-time
  dependencies.
- **Optional, lazily created members**, where `std::optional` would make the enclosing object too large.

Most objects don't need to be on the heap at all. A `std::unique_ptr` member that's always set could usually be a
plain member. Prefer values; use `unique_ptr` when you need heap allocation for one of the reasons above.

### `make_unique`

Prefer `std::make_unique<T>(args...)` over `std::unique_ptr<T>(new T(args...))`. It's shorter, never mentions
`new`, and (before C++17) avoided a leak in expressions like `f(std::unique_ptr<T>(new T), g())` if `g()` threw.

### Custom deleters

`unique_ptr` can own anything with a "release" function, which makes it a general RAII wrapper for C APIs:

```cpp
struct FileCloser { void operator()(std::FILE* f) const noexcept { std::fclose(f); } };
using FilePtr = std::unique_ptr<std::FILE, FileCloser>;

FilePtr f(std::fopen("ticks.bin", "rb"));
if (!f) throw std::runtime_error("open failed");
std::fread(buf, 1, n, f.get());
// fclose runs automatically
```

An empty function object as the deleter costs no space: `sizeof(FilePtr)` is still 8. A function pointer deleter
(`std::unique_ptr<std::FILE, int(*)(std::FILE*)>`) costs 8 more bytes and an indirect call, so prefer a stateless
struct or a lambda type:

```cpp
auto unmap = [len](void* p) { ::munmap(p, len); };   // stateful lambda: captures len
std::unique_ptr<void, decltype(unmap)> region(::mmap(...), unmap);
```

### Exercises

:::exercise Size check
What's `sizeof` for each, on x86-64? (a) `std::unique_ptr<int>`; (b) `std::unique_ptr<int[]>`;
(c) `std::unique_ptr<FILE, FileCloser>`; (d) `std::unique_ptr<FILE, int(*)(FILE*)>`;
(e) `std::unique_ptr<void, decltype(unmap)>` where `unmap` captures a `std::size_t`.
:::solution
(a) 8. (b) 8: the array form uses `delete[]` but stores the same single pointer. (c) 8: the empty deleter takes no
space. (d) 16: the function pointer is stored. (e) 16: the lambda's captured `size_t` is stored alongside the
pointer.
:::

---

## 2. `std::shared_ptr`: shared ownership

A `std::shared_ptr<T>` allows several owners. The object is destroyed when the last owner goes away. To track
that, every shared object has a **control block** holding:

- the **strong count**: how many `shared_ptr`s own the object;
- the **weak count**: how many `weak_ptr`s (plus one for the strong owners collectively) refer to the control block;
- the deleter and allocator, if custom.

```layout "A shared_ptr is two pointers: one to the object, one to the control block. With make_shared, the control block and the object share one allocation. Constructing from a raw new needs two."
title: std::shared_ptr<Update> (16 bytes)
row: 16
object_ptr:8:1 control_ptr:8:2

title: make_shared allocation: control block + object in one heap block
row: 48
vptr:8:6 strong:4:2 weak:4:2 Update_object:32:1
```

Copying a `shared_ptr` increments the strong count; destroying one decrements it. Because `shared_ptr`s may be
copied and destroyed on different threads, these updates are **atomic** read-modify-write operations
(`lock xadd` on x86). Each costs about 5–20 ns uncontended, and much more under contention, since every core
touching the same object fights over one cache line.

| | `unique_ptr<T>` | `shared_ptr<T>` |
|---|---|---|
| Size | 8 bytes | 16 bytes |
| Creation | one allocation | one (`make_shared`) or two allocations |
| Copy | not allowed | atomic increment |
| Move | copy a pointer | copy two pointers (no atomics) |
| Destruction | `delete` | atomic decrement; `delete` and free control block when it reaches zero |
| Dereference | one load | one load |

> [!NOTE]
> libstdc++ uses non-atomic counts in programs that never start a second thread (it checks whether libpthread is
> active). Don't rely on this; it changes the moment any library spawns a thread.

### `make_shared`

`std::make_shared<T>(args...)` allocates the control block and the object in **one** block, saving an allocation
and putting the counts next to the object. Prefer it. The trade-off: the memory isn't freed until the last
`weak_ptr` is gone too, because the control block can't be freed separately from the object.

### `weak_ptr`

A `std::weak_ptr<T>` refers to a shared object without owning it. It doesn't keep the object alive. To use the
object, you `lock()` it, which returns a `shared_ptr` (null if the object is gone):

```cpp
class Subscription {
    std::weak_ptr<Strategy> target_;
public:
    void deliver(const Quote& q) {
        if (auto s = target_.lock()) s->on_quote(q);   // object still alive: use it
        // else: the strategy was destroyed; drop the update (and unsubscribe)
    }
};
```

`weak_ptr` solves two problems: observing an object that may die (the callback case from Chapter 2.5), and
breaking **reference cycles**. If A holds a `shared_ptr` to B and B holds one to A, neither count can reach zero,
and both leak. Making one direction a `weak_ptr` fixes it.

### Exercises

:::exercise Count the atomics
How many atomic operations does this code perform on the reference count?

```cpp
void process(std::shared_ptr<const Update> u);   // by value

auto u = std::make_shared<const Update>(...);
for (int i = 0; i < 8; ++i) process(u);
```
:::solution
Each call copies `u` into the parameter (one atomic increment) and destroys the parameter at the end of the call
(one atomic decrement): 16 atomic operations, plus one decrement when `u` itself is destroyed. If `process` only
reads the update, taking `const Update&` instead does zero atomic operations (section 3).
:::

---

## 3. Passing smart pointers to functions

The rule follows from Chapter 1.4's ownership vocabulary: **a function's parameter should express what the function
does with ownership.**

| The function... | Parameter |
|---|---|
| just uses the object | `T&` or `const T&` (or `T*` if it may be null) |
| takes ownership | `std::unique_ptr<T>` by value (caller writes `std::move(p)`) |
| shares ownership (stores a copy) | `std::shared_ptr<T>` by value, then `std::move` it into place |
| might or might not keep a copy | `const std::shared_ptr<T>&` and copy only if needed |
| reseats the caller's pointer | `std::unique_ptr<T>&` / `std::shared_ptr<T>&` (rare) |

The most common mistake is passing `shared_ptr` by value to functions that only use the object, paying two atomic
operations per call for nothing:

```cpp
double mid(std::shared_ptr<const Book> book);   // bad: atomic inc/dec for every call
double mid(const Book& book);                   // good: callers write mid(*ptr)
```

The callee doesn't care how the caller manages the book's lifetime. A plain reference works with books owned by
`shared_ptr`, `unique_ptr`, a stack variable or a vector element alike.

---

## 4. When to use neither

On hot paths in low-latency systems, *both* smart pointers are often the wrong tool, because **heap allocation per
object** is itself the problem (Chapter 7.4). Common alternatives:

- **Values in containers.** A `std::vector<Order>` instead of `std::vector<std::unique_ptr<Order>>`: one
  allocation, contiguous, no pointer chasing.
- **Indices instead of pointers.** Store a `std::uint32_t` index into a pool or vector. Half the size of a pointer,
  stable across reallocation, and trivially copyable.
- **Object pools** (Chapter 7.5) that recycle a fixed set of preallocated objects.
- **Arenas**: allocate everything for one message or one session from a bump allocator, and free it all at once.
- **Single ownership with clear lifetimes.** If one component owns the data and outlives every consumer, consumers
  can just hold references.

For the market-data fan-out from the introduction, a typical low-latency design is: the producer writes each update
into a preallocated ring buffer (Chapter 11.3), and consumers read it in place. There's no reference counting at
all; the ring's sequence numbers define when a slot can be reused.

> [!IMPORTANT]
> `std::shared_ptr` is the right choice when ownership is *genuinely* shared and lifetimes are *genuinely* dynamic:
> caches, asynchronous I/O completions, plug-in objects, configuration snapshots swapped atomically. Reaching for it
> because ownership is unclear just hides the design problem, and adds atomics to every copy.

### Exercises

:::exercise Redesign
A feed handler allocates each parsed message with `std::make_shared<Message>()` and pushes it to two consumers
through queues of `std::shared_ptr<Message>`. List three costs of this design and propose an alternative.
:::solution
Costs: (1) a heap allocation and free per message, on the hot path; (2) atomic reference-count updates for every
copy and destruction, contended between the producer and two consumer threads; (3) messages scattered across the
heap, so consumers take cache misses reading them, plus 16-byte queue entries. Alternative: a preallocated ring
buffer of fixed-size `Message` slots, written in place by the producer; each consumer tracks its own read sequence
number, and the producer reuses a slot only after both consumers have passed it (a "disruptor" style design). No
allocation, no reference counts.
:::

---

## Summary

- `std::unique_ptr` expresses exclusive ownership at zero cost: one pointer, an inlined `delete`. Use
  `make_unique`.
- Custom deleters make `unique_ptr` an RAII wrapper for any C handle; stateless deleters add no size.
- `std::shared_ptr` adds a control block, 16-byte size, and an atomic increment/decrement on every copy/destroy.
  Use `make_shared` for one allocation.
- `weak_ptr` observes without owning; use it for callbacks to objects that may die and to break cycles.
- Pass `T&`/`const T&` to functions that only use the object; pass smart pointers only to express ownership
  transfer or sharing.
- On hot paths, prefer values, indices, pools and arenas over per-object heap allocation.

## Key terms

**Exclusive ownership** · **`std::unique_ptr`** · **custom deleter** · **pimpl** · **shared ownership** ·
**`std::shared_ptr`** · **control block** · **strong count / weak count** · **`make_shared`** · **`std::weak_ptr`** ·
**reference cycle** · **object pool**
