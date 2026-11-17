+++
title = "The Rule of Zero, Three and Five"
minutes = 40
practice_tags = ["raii", "move-semantics", "memory"]
+++

## Why this chapter matters

You write a small class that owns a buffer of prices:

```cpp
class PriceBuffer {
public:
    explicit PriceBuffer(std::size_t n) : data_(new double[n]{}), size_(n) {}
    ~PriceBuffer() { delete[] data_; }
private:
    double*     data_;
    std::size_t size_;
};
```

It compiles cleanly, and it's broken. The first time someone writes `PriceBuffer b = a;` (or puts it in a
`std::vector`, which copies on reallocation), the compiler-generated copy constructor copies the *pointer*. Now two
objects own the same array, and both destructors delete it. That's a double free: a crash if you're lucky, heap
corruption if you're not.

The compiler writes up to six **special member functions** for every class, unless you write them yourself. Knowing
which ones it writes, what they do, and when you must take over is the difference between classes that "just work"
in containers and classes that corrupt memory. Fortunately the rules boil down to three slogans.

By the end of this chapter you should be able to:

- list the six special members and say when the compiler declares each one;
- explain the Rule of Zero, and design classes that need no special members at all;
- implement the Rule of Five correctly for a resource-owning class, including self-assignment;
- explain why move operations should be `noexcept`.

---

## 1. The six special members

```cpp
class T {
public:
    T();                          // default constructor
    ~T();                         // destructor
    T(const T&);                  // copy constructor
    T& operator=(const T&);       // copy assignment
    T(T&&) noexcept;              // move constructor
    T& operator=(T&&) noexcept;   // move assignment
};
```

If you don't declare one, the compiler may declare it for you, implemented **memberwise**: the generated copy
constructor copies each base and member in order, the generated move constructor moves each, the generated
destructor destroys each.

Memberwise is exactly right when every member manages itself. `std::string`, `std::vector`, `int` and
`std::unique_ptr` all know how to copy, move or destroy themselves. It's exactly wrong when a member is a raw
handle whose *meaning* is ownership, like `double* data_` above: copying the pointer value doesn't copy what it
owns.

### When does the compiler generate them?

The rules are intricate, but these are the ones that matter:

| If you declare... | Then the compiler... |
|---|---|
| any constructor | doesn't generate the default constructor (write `T() = default;` to get it back) |
| a copy operation or destructor | still generates copies (deprecated), but **does not generate moves**: moves fall back to copies |
| a move operation | deletes the copy operations unless you declare them too |

The middle row is the subtle one. Add a destructor to a class (perhaps just to log something) and it silently
loses its move operations. Every "move" becomes a copy. The code still compiles; it's just slower.

You can be explicit about any of them with `= default` (generate the memberwise version) or `= delete` (forbid
it).

### Exercises

:::exercise What does it get?
Which special members does each class have, and are its moves real moves or copies?
(a) `struct A { std::string s; };`
(b) `struct B { std::string s; ~B() { std::puts("bye"); } };`
(c) `struct C { std::unique_ptr<int> p; };`
:::solution
(a) All six, memberwise. Moves move the string.
(b) Default constructor, destructor, copy constructor and copy assignment. Because a destructor is user-declared,
no move operations are generated, so `B b2 = std::move(b1);` **copies** the string.
(c) Default constructor, destructor, move constructor and move assignment. The copy operations are deleted, because
`unique_ptr`'s copies are deleted, so memberwise copy is impossible. `C` is move-only.
:::

---

## 2. The Rule of Zero

> **If your class doesn't directly manage a resource, declare none of the special members.**

Let members that already manage themselves do the work:

```cpp
class PriceBuffer {
public:
    explicit PriceBuffer(std::size_t n) : data_(n) {}
    double& operator[](std::size_t i) { return data_[i]; }
    std::size_t size() const { return data_.size(); }
private:
    std::vector<double> data_;   // copies, moves and frees itself
};
```

That's the whole fix for the introduction's bug. `PriceBuffer` now copies deeply, moves cheaply, and frees its
memory, with zero lines of special-member code. Each resource has exactly one class responsible for it
(`std::vector` here), and everything built on top composes for free.

The Rule of Zero is the goal for nearly every class you write. Application classes like `Strategy`, `OrderBook`,
`RiskLimits` and `Config` should be made of values, containers and smart pointers, and declare no special members.

> [!TIP]
> If you want a move-only class, you often still don't need to write anything: one `std::unique_ptr` member makes
> the whole class move-only automatically.

---

## 3. The Rule of Three (and why it became five)

> **If you need to write a destructor, a copy constructor or a copy assignment, you almost certainly need all
> three.**

A custom destructor means the class owns something the default destruction wouldn't release. That same something
won't be copied correctly by the default copy either. This was the classic rule in C++98.

C++11 added moves, so a resource-owning class now has five operations to consider:

> **Rule of Five:** if you define any of destructor, copy constructor, copy assignment, move constructor, move
> assignment, define (or `= default` / `= delete`) all five.

Here is `PriceBuffer` done by hand, as a container implementer would write it:

```cpp
class PriceBuffer {
public:
    explicit PriceBuffer(std::size_t n) : data_(n ? new double[n]{} : nullptr), size_(n) {}

    ~PriceBuffer() { delete[] data_; }

    // Copy: allocate a new array and copy the contents (a "deep copy").
    PriceBuffer(const PriceBuffer& other)
        : data_(other.size_ ? new double[other.size_] : nullptr), size_(other.size_) {
        std::copy_n(other.data_, size_, data_);
    }

    // Move: steal the pointer and leave the source empty but valid.
    PriceBuffer(PriceBuffer&& other) noexcept
        : data_(std::exchange(other.data_, nullptr)), size_(std::exchange(other.size_, 0)) {}

    // One assignment operator handles both copy and move: copy-and-swap.
    PriceBuffer& operator=(PriceBuffer other) noexcept {
        swap(other);
        return *this;
    }

    void swap(PriceBuffer& other) noexcept {
        std::swap(data_, other.data_);
        std::swap(size_, other.size_);
    }

    double& operator[](std::size_t i) { return data_[i]; }
    std::size_t size() const { return size_; }

private:
    double*     data_;
    std::size_t size_;
};
```

A few things to notice:

- **The moved-from state.** After the move constructor, `other` has `data_ == nullptr` and `size_ == 0`. It's a
  valid, empty buffer: its destructor is harmless, and it can be assigned to again. That's the general contract
  for moved-from objects in C++: *valid but unspecified*. You may destroy or assign to them; don't assume anything
  else about their value unless the type documents it.
- **`std::exchange(x, v)`** sets `x` to `v` and returns the old value. It's made for move constructors.
- **Copy-and-swap.** `operator=` takes its parameter **by value**. If the caller passes an lvalue, the parameter is
  copy-constructed; if an rvalue, it's move-constructed. Then we swap our contents with it, and the parameter's
  destructor frees our old array. This one function is both copy and move assignment, it's automatically safe
  for self-assignment (`a = a`), and it's exception-safe: if the copy throws, it throws before `*this` is touched.

> [!PERF]
> Copy-and-swap is elegant but not always optimal. When assigning a buffer to another buffer of the same size, it
> allocates a new array anyway, where a hand-written copy assignment could reuse the existing storage. That's
> why `std::vector` writes its assignment operators by hand. For most classes the simplicity wins.

### Self-assignment

If you write copy assignment the "obvious" way, check what happens when `this == &other`:

```cpp
PriceBuffer& operator=(const PriceBuffer& other) {
    delete[] data_;                          // if &other == this, we just deleted other's data too
    data_ = new double[other.size_];
    std::copy_n(other.data_, other.size_, data_);   // reads freed memory
    size_ = other.size_;
    return *this;
}
```

Self-assignment rarely appears literally, but it happens through aliases: `book.levels[i] = book.levels[j]` with
`i == j`. Copy-and-swap, or allocating the new buffer *before* deleting the old one, avoids the problem.

### Exercises

:::exercise The forgotten copy
A class has a user-written destructor that `delete`s a raw pointer member, and nothing else. List two operations
that will cause a double free.
:::solution
Any copy: `T b = a;` (copy constructor), `b = a;` (copy assignment, which also leaks `b`'s original pointer),
passing a `T` by value, returning a named local in a way that copies, or storing it in a `std::vector` that copies
elements on reallocation. Notably, "moving" it doesn't help: with a user-declared destructor no move constructor is
generated, so `std::move(a)` also copies the pointer.
:::

:::exercise Moved-from
After `PriceBuffer b = std::move(a);`, which of these are safe? (a) `a.size()`; (b) `a[0]`; (c) `a = b;`;
(d) letting `a` go out of scope.
:::solution
(a) Safe, and for this class it returns 0 (our move constructor documents that). (b) Unsafe: `a` is empty, so
`a[0]` dereferences a null pointer. (c) Safe: assigning a new value to a moved-from object is always allowed.
(d) Safe: destroying a moved-from object is always allowed, and here `delete[] nullptr` is a no-op.
:::

---

## 4. Why moves must be `noexcept`

Consider what `std::vector<T>::push_back` does when it runs out of capacity: allocate a bigger array, transfer the
existing elements, free the old array. Should it **move** or **copy** the existing elements?

Moving is faster. But vector guarantees that if `push_back` throws, the vector is unchanged (the "strong exception
guarantee"). If it's halfway through moving elements and a move constructor throws, the old array is already
partly gutted, and the guarantee is broken. Copying leaves the old array intact.

So `std::vector` uses `std::move_if_noexcept`: it moves elements only if the move constructor is `noexcept` (or the
type isn't copyable at all). Otherwise it **copies**.

```cpp
struct Slow { std::string s; Slow(Slow&& o) : s(std::move(o.s)) {} /* no noexcept */ ... };
std::vector<Slow> v;  // every reallocation copies every string
```

A missing `noexcept` on a move constructor turns every vector reallocation into a full deep copy. Compiler-
generated moves are `noexcept` automatically when all members' moves are. When you write one by hand, add
`noexcept` yourself. Moves that just shuffle pointers can't throw anyway.

You can check it at compile time:

```cpp
static_assert(std::is_nothrow_move_constructible_v<PriceBuffer>);
```

### Exercises

:::exercise Measure it
Write a struct `Holder` with a `std::vector<double>` member of 1,000 elements and a hand-written move constructor.
Push 100,000 `Holder`s into a `std::vector<Holder>` without `reserve`, once with `noexcept` on the move constructor
and once without. Predict the difference before you measure.
:::solution
Without `noexcept`, every reallocation copies every existing `Holder`, including its 8 KB inner vector. With about
17 reallocations for 100,000 elements, the total copied is roughly 2× the final size (geometric growth), i.e.
about 1.6 GB of copying, plus an allocation per copy. With `noexcept`, reallocation just moves three pointers per
element. Expect the `noexcept` version to be one to two orders of magnitude faster. (Calling `reserve` first would
remove the reallocations in both versions.)
:::

---

## 5. A decision procedure

When you write a class:

1. **Can every member manage itself?** Use values, standard containers, `std::unique_ptr`, `std::string`. Then
   declare **nothing**. Rule of Zero. This covers nearly every class.
2. **Should it be non-copyable?** (It represents a unique thing: a socket, a thread, a lock, a strategy instance.)
   A `std::unique_ptr` member does it automatically, or explicitly `= delete` the copy operations and `= default`
   the moves if they're valid.
3. **Does it directly own a raw resource?** Write a small dedicated RAII class that owns *only* that resource and
   follows the Rule of Five. Then use it as a member elsewhere, which brings you back to Rule of Zero.
4. **Polymorphic base class?** Give it a `public virtual` destructor (or a `protected` non-virtual one). Declaring
   the destructor suppresses implicit moves, so `= default` the other four if you want them, or delete copies to
   prevent slicing.

```cpp
class Strategy {   // polymorphic base
public:
    virtual ~Strategy() = default;
    Strategy() = default;
    Strategy(const Strategy&) = delete;             // strategies are unique; also prevents slicing
    Strategy& operator=(const Strategy&) = delete;
    virtual void on_quote(const Quote& q) = 0;
};
```

> [!NOTE]
> Why a virtual destructor? Deleting a `Derived` through a `Base*` (as `std::unique_ptr<Base>` does) calls `~Base`
> only, unless the destructor is virtual. That's undefined behaviour, and in practice it skips `Derived`'s
> members' destructors.

---

## Summary

- The six special members are the default constructor, destructor, copy constructor and assignment, and move
  constructor and assignment. The compiler generates memberwise versions unless you declare them.
- Declaring a destructor or copy operation suppresses implicit moves: "moves" silently become copies.
- **Rule of Zero:** build classes from members that manage themselves, and declare no special members.
- **Rule of Five:** a class that directly owns a resource defines or defaults/deletes all five.
- Moved-from objects must be valid but unspecified: destroyable and assignable.
- Copy-and-swap gives one exception-safe, self-assignment-safe assignment operator.
- Mark move operations `noexcept`, or `std::vector` will copy instead of move on reallocation.

## Key terms

**Special member function** · **memberwise copy** · **deep copy** · **Rule of Zero** · **Rule of Three** ·
**Rule of Five** · **moved-from state** · **copy-and-swap** · **self-assignment** · **strong exception guarantee** ·
**`std::move_if_noexcept`**
