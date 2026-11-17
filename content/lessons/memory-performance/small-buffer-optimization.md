+++
title = "Small-Buffer Optimization and Inline Storage"
minutes = 35
practice_tags = ["memory", "templates", "move-semantics"]
+++

## Why this chapter matters

An execution message can report several fills, but almost always one to six. A handler collects them into a
`std::vector<Fill>`, processes them, and throws the vector away. Measured on the test machine over two million
messages:

| Container | Time per message |
|---|---|
| `std::vector<Fill>` | 57.3 ns |
| `std::vector<Fill>` with `reserve(8)` | 33.6 ns |
| `SmallVector<Fill, 8>` (inline storage for 8) | 17.8 ns |

The plain vector grows through capacities 1, 2, 4, 8, allocating and copying each time. Reserving helps, but still
costs one `malloc` and `free` per message. The small vector keeps up to eight fills **inside the object itself**, on
the stack, and only touches the heap if a message has more than eight. For the common case, it's 3× faster and never
allocates.

This is the **small-buffer optimization (SBO)**: reserve space for a small number of elements inside the object, and
fall back to the heap only for the rare large case. You've already met it in `std::string` (Chapter 4.5) and
`std::function` (Chapter 2.5). This chapter shows how it works, how to build containers and callables that use it,
and what it costs.

By the end of this chapter you should be able to:

- explain the SBO trade-off: object size and branches versus allocations;
- describe how `std::string`, `std::function` and similar types use inline storage;
- implement a small vector with inline capacity and heap fallback;
- choose among fixed-capacity, small-buffer and heap containers for a given size distribution.

---

## 1. The trade-off

A container with inline storage for N elements:

- **Never allocates** while it holds ≤ N elements. No `malloc` cost, no allocator variance, no page faults.
- Keeps elements **next to the container's own fields**, so accessing them doesn't require following a pointer to
  somewhere else in memory: often the same cache line as the size field.
- Is **bigger**: `sizeof` includes room for N elements whether used or not. A `SmallVector<Fill, 8>` is 152 bytes; a
  `std::vector<Fill>` is 24.
- Has an extra **branch** on some operations ("am I inline or on the heap?"), and **moving** it is no longer a pointer
  swap when the data is inline: the elements must be moved one by one.

So SBO wins when the size distribution is **heavily skewed towards small sizes** and objects are **short-lived or
local**. It loses when the containers are long-lived members of objects stored in large numbers (the inline buffer
bloats every one of them), or when sizes are usually large anyway.

> [!NOTE]
> Choose N from the measured distribution: large enough to cover, say, 95–99% of cases, but not so large that the
> object becomes huge. For fills per execution message, 4 or 8. For legs of a multi-leg order, 4. For symbols, 8
> or 16 characters.

---

## 2. SBO in the standard library

**`std::string`**: libstdc++ stores up to 15 characters inside its 32-byte object, reusing the space that would hold
the heap capacity (Chapter 4.5). libc++ fits 22 characters in 24 bytes with a more aggressive bit layout.

**`std::function`**: libstdc++ has a 16-byte internal buffer for callables that are small and trivially copyable;
larger ones are heap-allocated (Chapter 2.5 measured exactly this). Calling through it costs an indirect call either
way: 2.35 ns per call on the test machine, against 0.32 ns for calling the lambda directly in the same loop.

**`std::any`** uses a small buffer too. **`std::vector`** does **not**: the standard's requirement that moving a
vector never invalidates pointers to its elements rules out inline storage, since moving inline elements would change
their addresses.

Third-party implementations are common in performance-oriented codebases: `boost::container::small_vector`,
`absl::InlinedVector`, `llvm::SmallVector`, `folly::small_vector`. C++26 adds `std::inplace_vector<T, N>`: a vector
with fixed capacity N, stored entirely inline, never allocating (it throws or returns an error when full).

---

## 3. Building a small vector

Here is a minimal small vector for trivially copyable types, which keeps the logic short. (Supporting arbitrary types
means placement-constructing and destroying elements individually and using move constructors when spilling, as
production versions do.)

```cpp
template <class T, std::size_t N>
    requires std::is_trivially_copyable_v<T> && std::is_trivially_destructible_v<T>
class SmallVector {
public:
    SmallVector() = default;
    SmallVector(const SmallVector&) = delete;              // kept short: copy/move omitted
    SmallVector& operator=(const SmallVector&) = delete;
    ~SmallVector() { if (data_ != inline_buf()) std::free(data_); }

    void push_back(const T& v) {
        if (size_ == cap_) grow();                         // rare once N is chosen well
        data_[size_++] = v;
    }
    T& operator[](std::size_t i) { return data_[i]; }
    std::size_t size() const { return size_; }
    bool is_inline() const { return data_ == inline_buf(); }
    T* begin() { return data_; }
    T* end() { return data_ + size_; }

private:
    T* inline_buf() { return reinterpret_cast<T*>(buf_); }
    const T* inline_buf() const { return reinterpret_cast<const T*>(buf_); }

    void grow() {                                          // spill to the heap, doubling capacity
        std::size_t new_cap = cap_ * 2;
        T* p = static_cast<T*>(std::malloc(new_cap * sizeof(T)));
        if (!p) throw std::bad_alloc{};
        std::memcpy(p, data_, size_ * sizeof(T));          // fine: T is trivially copyable
        if (data_ != inline_buf()) std::free(data_);
        data_ = p;
        cap_ = new_cap;
    }

    alignas(T) std::byte buf_[N * sizeof(T)];              // the inline storage
    T* data_ = inline_buf();                               // points at buf_ or at the heap
    std::size_t size_ = 0, cap_ = N;
};
```

Design notes:

- **`data_` always points at the live storage**, so `operator[]`, `begin` and `end` have no branch at all; only
  `push_back`'s growth path and the destructor check where the data is.
- **The buffer is raw bytes**, not `T buf_[N]`, so constructing the container doesn't construct N elements.
- **Copy and move are deleted here** to keep the example small. Moving a small vector whose data is inline must copy
  the elements into the destination and repoint `data_` there; a defaulted move would leave `data_` pointing into the
  *source* object's buffer, a dangling pointer. This is the single most common bug in hand-written SBO types, and the
  reason the practice problem "Small Vector with Inline Storage" tests moves carefully.

### Exercises

:::exercise The move bug
Suppose `SmallVector` had a defaulted move constructor. Walk through what happens with `SmallVector<int, 4> a;
a.push_back(1); SmallVector<int, 4> b = std::move(a);` and then `a` goes out of scope.
:::solution
The defaulted move copies the bytes of `buf_` (fine: `b.buf_` now holds `1`), but also copies `data_`, which points to
**`a.buf_`**, not `b.buf_`. `b` now reads and writes through a pointer into `a`. When `a` is destroyed (or reused),
`b` is reading a dead object's storage: undefined behaviour, and ASan reports a stack-use-after-scope. The fix: in the
move constructor, if the source is inline, copy elements into `this->buf_` and set `data_ = inline_buf()`; if it's on
the heap, steal the pointer and reset the source to its own inline buffer.
:::

---

## 4. Inline callables

The same idea fixes `std::function`'s allocation problem: an **inplace function** with a fixed, larger internal
buffer, which refuses (at compile time) callables that don't fit:

```cpp
template <class Sig, std::size_t Cap = 48> class InplaceFunction;

template <class R, class... Args, std::size_t Cap>
class InplaceFunction<R(Args...), Cap> {
public:
    template <class F>
    InplaceFunction(F f) {
        static_assert(sizeof(F) <= Cap, "callable too large for InplaceFunction");
        static_assert(std::is_trivially_copyable_v<F> && std::is_trivially_destructible_v<F>,
                      "kept simple: trivially copyable callables only");
        new (buf_) F(std::move(f));
        call_ = [](void* p, Args... a) -> R { return (*static_cast<F*>(p))(std::forward<Args>(a)...); };
    }
    R operator()(Args... a) { return call_(buf_, std::forward<Args>(a)...); }
private:
    alignas(std::max_align_t) std::byte buf_[Cap];
    R (*call_)(void*, Args...);
};
```

It never allocates, and storing one in a ring buffer of tasks or callbacks keeps everything contiguous. A call still
goes through a function pointer, so it costs about what `std::function` does per call; the saving is the allocation
and the pointer chase to a heap-allocated callable. Production versions (e.g. `stdext::inplace_function`, SG14) also
handle non-trivial callables with a small table of copy/destroy functions.

---

## 5. Choosing a container by size distribution

| Size distribution | Container |
|---|---|
| Always ≤ N, known at compile time | `std::array<T, N>` with a count, or `std::inplace_vector<T, N>` (C++26): no heap ever |
| Usually ≤ N, occasionally more | small vector with inline N |
| Usually large, or unpredictable | `std::vector`, reused across calls (Chapter 4.2) |
| Long-lived member of many objects, usually small | `std::vector` (24 bytes) or an index into a shared pool, to avoid bloating every object |

> [!TIP]
> On a hot path, "occasionally more" deserves thought: the spill to the heap is exactly the rare slow event that
> shows up in p99.9 (Chapter 6.4). If the maximum is bounded by the protocol (an exchange may allow at most 255 fills
> per message, say), a fixed-capacity container sized for the maximum may be better than one that can allocate.

---

## Summary

- SBO stores small contents inside the object and falls back to the heap only when they don't fit. For skewed size
  distributions it removes nearly all allocations: 3× faster than `std::vector` in this chapter's benchmark.
- The costs are a bigger object, an occasional branch, and moves that copy inline elements.
- `std::string` and `std::function` use SBO; `std::vector` can't. Third-party small vectors and C++26's
  `std::inplace_vector` fill the gap.
- Keep `data_` pointing at the live storage so element access is branch-free, and implement moves carefully: a
  defaulted move leaves a pointer into the source.
- Pick N from measured distributions, and prefer fixed-capacity containers when the maximum is bounded.

## Key terms

**Small-buffer optimization (SBO)** · **inline storage** · **heap fallback / spill** · **small vector** ·
**`std::inplace_vector`** · **inplace function** · **size distribution**
