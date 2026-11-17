+++
title = "Inside std::vector"
minutes = 40
practice_tags = ["stl", "memory", "move-semantics"]
+++

## Why this chapter matters

A strategy collects candidate orders into a `std::vector<Order>` on every tick, then sends the best few. Profiling
shows 15% of the tick handler is inside `malloc` and `free`: every tick, the vector starts empty, grows through
capacities 1, 2, 4, 8, 16, 32, reallocating and copying each time, and frees everything at the end. Moving the
vector out of the function and calling `clear()` instead of re-creating it removes all of that.

`std::vector` is the container you'll use most, and the one the rest of this book assumes. It's simple enough to
understand completely: three pointers and a growth rule. Once you know exactly what it does, you can predict when
it allocates, when it copies, and when your pointers into it become invalid.

By the end of this chapter you should be able to:

- describe the layout of a vector and the meaning of size and capacity;
- explain geometric growth and amortized O(1) `push_back`;
- use `reserve`, `clear`, `shrink_to_fit` and `emplace_back` deliberately;
- list exactly which operations invalidate iterators, pointers and references;
- reuse vectors on hot paths so they never allocate in steady state.

---

## 1. Layout: three pointers

A `std::vector<T>` object is 24 bytes on x86-64: three pointers.

```layout "The vector object itself (on the stack or inside another object) is just three pointers. The elements live in a separate heap block with room for capacity() elements, of which the first size() are constructed."
title: the std::vector<std::int64_t> object
row: 24
begin:8:1 end:8:2 end_of_storage:8:3

title: its heap block (capacity 8, size 5)
row: 64
p0:8:1 p1:8:1 p2:8:1 p3:8:1 p4:8:1 spare:8:6 spare:8:6 spare:8:6
```

- `size() = end - begin`: how many elements are constructed.
- `capacity() = end_of_storage - begin`: how many fit before the next reallocation.
- `data()` returns `begin`. Elements are contiguous, so `data()` can be passed to C APIs, `memcpy`, SIMD loops, or
  a `std::span`.

`v[i]` compiles to a single load from `begin + i * sizeof(T)`, with no bounds check. `v.at(i)` checks and throws
`std::out_of_range`. In debug builds, libstdc++ can check `operator[]` too, with `-D_GLIBCXX_ASSERTIONS`, which is
cheap enough that some firms leave it on in testing.

---

## 2. Growth

When `push_back` finds `size() == capacity()`, the vector must reallocate:

1. Allocate a new block, **larger by a constant factor** (2× in libstdc++ and libc++, 1.5× in MSVC).
2. Move (or copy) the existing elements into it (see section 4).
3. Construct the new element.
4. Destroy the old elements and free the old block.

That's O(n) work for one `push_back`. But because capacity grows geometrically, reallocations get exponentially
rarer. Pushing *n* elements one at a time into an empty vector causes about log₂(n) reallocations, which copy
1 + 2 + 4 + ... + n/2 < n elements in total. So the **amortized** cost of `push_back` is O(1): each element is
copied fewer than once on average, plus about one extra element copy per push for the reallocations.

Amortized O(1) is good for throughput. It's bad for latency: most pushes take a couple of nanoseconds, and every
so often one takes microseconds while it allocates and copies a large block. On a hot path, that's exactly the
outlier you can't afford.

```cpp
std::vector<int> v;
for (int i = 0; i < 20; ++i) {
    v.push_back(i);
    std::printf("size=%zu capacity=%zu\n", v.size(), v.capacity());
}
// libstdc++: capacity goes 1, 2, 4, 4, 8, 8, 8, 8, 16, ..., 32
```

### `reserve`

If you know (or can bound) how many elements you'll need, allocate once up front:

```cpp
std::vector<Fill> fills;
fills.reserve(msg.fill_count());   // one allocation
for (...) fills.push_back(parse_fill(...));   // no reallocations
```

`reserve(n)` sets the capacity to at least `n` without changing the size. It never shrinks. It's one of the
simplest and most effective optimizations in C++ code.

> [!WARNING]
> Don't call `reserve` inside a loop with a slowly increasing value, like `v.reserve(v.size() + 1)` before each
> push. That defeats geometric growth: each call reallocates to exactly the requested size, turning *n* pushes into
> O(n²) copying. Reserve once, for the final size.

### Exercises

:::exercise Count the copies
Pushing 1,000 elements one at a time into an empty `std::vector` with doubling growth: how many reallocations
happen, and how many element moves in total?
:::solution
Capacities go 1, 2, 4, ..., 512, 1024: reallocations happen when growing from 1→2, 2→4, ..., 512→1024, which is 10
reallocations. They move 1 + 2 + 4 + ... + 512 = 1,023 elements in total. So about one extra move per element on
average, which is amortized O(1). With `reserve(1000)` first: one allocation, zero moves.
:::

---

## 3. Size vs capacity: reuse instead of reallocating

`clear()` destroys the elements and sets the size to zero, but **keeps the capacity**. The memory stays allocated
for reuse. That's the key to allocation-free hot paths:

```cpp
class Strategy {
public:
    void on_tick(const Book& book) {
        candidates_.clear();                     // size 0, capacity unchanged: no free
        for (const auto& lvl : book.levels())
            if (is_candidate(lvl)) candidates_.push_back(make_order(lvl));   // no malloc after warm-up
        send_best(candidates_);
    }
private:
    std::vector<Order> candidates_;              // lives across ticks; grows to its high-water mark once
};
```

After the first few ticks, `candidates_` has grown to the largest size it's ever needed, and from then on it never
allocates. Combined with a `reserve` at start-up for the expected maximum, it never allocates at all on the hot
path. This pattern, **reusing buffers across iterations**, is one of the most common techniques in low-latency
code.

To actually release memory:

- `v.shrink_to_fit()` is a *non-binding* request to reduce capacity to size. In practice, libstdc++ and libc++
  honour it by reallocating.
- `std::vector<T>().swap(v)` or `v = std::vector<T>()` is the guaranteed way to free everything.

> [!TIP]
> `resize(n)` changes the **size**, constructing (value-initializing) new elements or destroying excess ones.
> `reserve(n)` changes only the **capacity**. Writing to `v[i]` for `i >= size()` after a `reserve` is undefined
> behaviour, even though the memory is allocated: those elements haven't been constructed.

---

## 4. Move or copy on reallocation

When reallocating, the vector must transfer the existing elements to the new block. As Chapter 2.3 explained,
it **moves** them only if `T`'s move constructor is `noexcept` (or `T` isn't copyable). Otherwise it copies, to
keep the strong exception guarantee.

For **trivially copyable** types, libstdc++ goes further and transfers the whole block with a single `memmove`,
which runs at memory bandwidth.

| Element type | Reallocation transfers elements by |
|---|---|
| Trivially copyable (`int`, `double`, POD structs) | one `memmove` of the whole block |
| `noexcept` move constructor (`std::string`, `std::vector`, most well-written types) | moving each element |
| Move constructor *not* `noexcept`, copyable | **copying** each element |
| Move-only (copy deleted), move not `noexcept` | moving each element (no strong guarantee) |

So a vector of `std::string` reallocates cheaply (each move is a few pointer copies), while a vector of a class
with a hand-written, non-`noexcept` move constructor copies every string every time it grows.

### `emplace_back` vs `push_back`

```cpp
std::vector<Order> v;
v.push_back(Order{id, Side::Buy, px, qty});   // constructs a temporary, then moves it in
v.emplace_back(id, Side::Buy, px, qty);       // constructs directly in the vector's storage
```

`emplace_back` forwards its arguments (Chapter 2.4) to the element's constructor, skipping the temporary. For
small trivially copyable types the difference compiles away. For types with expensive moves it's a real saving.
Since C++17, `emplace_back` returns a reference to the new element.

One caution: `emplace_back` will call **explicit** constructors that `push_back` wouldn't, because it's direct
initialization. `std::vector<std::unique_ptr<Order>> v; v.emplace_back(raw_ptr);` compiles silently, and takes
ownership of a pointer that might not be yours to own.

---

## 5. Invalidation

Because a vector's elements live in one block that may be replaced, iterators, pointers and references into it
can **dangle** after modifications. The rules:

| Operation | Invalidates |
|---|---|
| `push_back`, `emplace_back`, `insert`, `resize` (growing), `reserve` **that reallocates** | **everything**: all iterators, pointers, references |
| the same, **without** reallocation (enough capacity) | `end()`, and for `insert`, everything at or after the insertion point |
| `erase`, `pop_back`, `resize` (shrinking) | everything at or after the erased position, and `end()` |
| `clear` | everything |
| `swap` | nothing, but iterators now refer to elements in the *other* vector |
| reading, `operator[]`, `at`, iterating | nothing |

The bug that this table prevents:

```cpp
std::vector<Order> orders = load();
for (const Order& o : orders)
    if (o.needs_hedge()) orders.push_back(make_hedge(o));   // may reallocate mid-loop: o and the loop iterators dangle
```

Fixes: collect into a separate vector and append afterwards; or iterate by index (`for (std::size_t i = 0; i <
orders.size(); ++i)`), since indices remain valid; or `reserve` enough capacity first (fragile, because someone
will change the reserve size).

### Erasing while iterating

`erase` returns an iterator to the element after the erased one, so the correct loop is:

```cpp
for (auto it = orders.begin(); it != orders.end(); )
    it = it->filled() ? orders.erase(it) : std::next(it);
```

But that's O(n²) when many elements are erased, since each `erase` shifts everything after it. The idiomatic
O(n) way is the **erase-remove idiom**, which C++20 wraps as `std::erase_if`:

```cpp
std::erase_if(orders, [](const Order& o) { return o.filled(); });   // one pass, each survivor moved at most once
```

If the order of elements doesn't matter, **swap-and-pop** erases one element in O(1):

```cpp
void erase_unordered(std::vector<Order>& v, std::size_t i) {
    v[i] = std::move(v.back());
    v.pop_back();
}
```

### Exercises

:::exercise Is it safe?
Which of these are safe?
(a) `int* p = &v[0]; v.reserve(v.capacity() * 2); *p = 1;`
(b) `v.reserve(100); int* p = &v[0]; for (int i = 0; i < 50; ++i) v.push_back(i); *p = 1;` (with `v.size() == 10`
initially)
(c) `auto it = v.begin() + 3; v.erase(v.begin()); *it = 1;`
(d) `auto it = v.begin(); v.erase(v.end() - 1); *it = 1;` (with `v.size() >= 2`)
:::solution
(a) Unsafe: reserving more than the current capacity reallocates, so `p` dangles. (b) Safe: capacity is at least
100 and the size only reaches 60, so no reallocation happens and `p` stays valid. (c) Unsafe: erasing at position 0
invalidates all iterators at or after it, including `it`. (In practice `it` now points to what was element 4, but
it's formally invalid.) (d) Safe: erasing the last element only invalidates iterators at or after it; `begin()` is
before it.
:::

:::exercise Remove filled orders
Explain why the erase-in-a-loop version above is O(n²) in the worst case, and why `std::erase_if` is O(n).
:::solution
Each `erase` shifts every following element down by one. If k of n elements are erased, that's up to k × n moves,
O(n²) when k is proportional to n. `std::erase_if` (remove-erase) makes one pass with a read and a write index:
each kept element is moved at most once to its final position, then the tail is destroyed in one `erase`. Total
O(n).
:::

---

## Summary

- A vector is three pointers (`begin`, `end`, `end_of_storage`) and one contiguous heap block.
- Capacity grows geometrically (2× in libstdc++), so `push_back` is amortized O(1), but individual pushes can be
  very slow when they reallocate.
- `reserve` once for the expected size. `clear` keeps capacity, so reuse long-lived vectors on hot paths.
- Reallocation moves elements only if their move constructor is `noexcept`; trivially copyable types are
  `memmove`d.
- `emplace_back` constructs in place, but can call explicit constructors.
- Reallocation invalidates everything; insert and erase invalidate from the position onward. Indices survive.
- Use `std::erase_if` for bulk removal and swap-and-pop when order doesn't matter.

## Key terms

**Size** · **capacity** · **geometric growth** · **amortized O(1)** · **`reserve`** · **buffer reuse** ·
**`emplace_back`** · **iterator invalidation** · **erase-remove idiom** · **swap-and-pop**
