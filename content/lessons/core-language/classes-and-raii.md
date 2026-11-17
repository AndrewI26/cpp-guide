+++
title = "Classes and RAII"
minutes = 40
practice_tags = ["raii", "memory"]
+++

## Why this chapter matters

Here is a function from a market-data handler:

```cpp
void process_snapshot(const char* path) {
    FILE* f = std::fopen(path, "rb");
    char* buf = static_cast<char*>(std::malloc(1 << 20));
    pthread_mutex_lock(&book_mutex);

    if (!read_header(f, buf)) return;          // leaks buf, leaks f, never unlocks the mutex
    apply(buf);                                // if this throws: same three leaks
    pthread_mutex_unlock(&book_mutex);
    std::free(buf);
    std::fclose(f);
}
```

Every early return and every exception needs its own cleanup, in reverse order. Miss one, and you leak a file
descriptor, or worse, leave the book mutex locked so every other thread hangs. This style of code is the source of
a huge fraction of resource bugs in C.

C++'s answer is one of the most important ideas in the language: **RAII**, Resource Acquisition Is
Initialization. Tie each resource to an object whose destructor releases it. The compiler guarantees destructors
run when objects go out of scope, on every path, including exceptions. Cleanup becomes automatic and impossible to
forget.

By the end of this chapter you should be able to:

- write a class with a constructor that establishes an invariant and members that preserve it;
- use member initializer lists correctly, and know the order members are initialized in;
- explain exactly when destructors run, and in what order;
- write RAII wrappers for files, locks and other resources, and recognize the standard library's ones.

---

## 1. Classes: data plus invariants

A `struct` and a `class` are the same thing in C++, except that members are public by default in a `struct` and
private by default in a `class`. Convention:

- `struct` for plain aggregates of data with no invariants: `Quote`, `Fill`, `Config`.
- `class` when there's an **invariant**, a rule about the data that must always hold, which you protect by
  making the data private and only changing it through member functions.

```cpp
class PriceLevel {
public:
    explicit PriceLevel(std::int64_t price) : price_(price) {}

    void add(std::int64_t qty) {
        assert(qty > 0);
        total_qty_ += qty;
        ++order_count_;
    }
    void remove(std::int64_t qty) {
        assert(qty > 0 && qty <= total_qty_ && order_count_ > 0);
        total_qty_ -= qty;
        --order_count_;
    }

    std::int64_t price() const { return price_; }
    std::int64_t total_qty() const { return total_qty_; }
    bool empty() const { return order_count_ == 0; }

private:
    std::int64_t price_;
    std::int64_t total_qty_ = 0;    // invariant: equals the sum of resting quantities,
    std::uint32_t order_count_ = 0; //            and is 0 exactly when order_count_ is 0
};
```

If `total_qty_` were public, any code anywhere could break the invariant, and you'd have to audit the whole program
to find out who. With it private, only five functions can touch it.

> [!NOTE]
> Encapsulation in C++ has **zero run-time cost**. Accessors like `price()` are defined in the class body, so
> they're inline and compile to a single load. You never have to trade safety for speed here.

---

## 2. Constructors

A **constructor** has the class's name and no return type. Its job is to turn raw memory into a valid object, so
that the invariant holds from the moment the object exists.

### Member initializer lists

Members are initialized **before** the constructor body runs. The initializer list says how:

```cpp
class Instrument {
public:
    Instrument(std::string symbol, std::int64_t tick_size)
        : symbol_(std::move(symbol)), tick_size_(tick_size)   // initialization
    {
        if (tick_size_ <= 0) throw std::invalid_argument("tick size must be positive");
    }
private:
    std::string  symbol_;
    std::int64_t tick_size_;
};
```

Writing `symbol_ = symbol;` in the body instead would first default-construct `symbol_` (an empty string), then
assign to it. That's wasted work, and for members that are `const`, references, or have no default constructor,
it doesn't compile at all.

> [!WARNING]
> Members are initialized in the order they're **declared in the class**, not the order in the initializer list.
> If `tick_size_` were declared before `symbol_`, and you wrote `: symbol_(...), tick_size_(symbol_.size())`,
> `tick_size_` would be initialized first, from an empty or unconstructed `symbol_`. `-Wall` warns about mismatched
> order (`-Wreorder`); keep the list in declaration order.

### Default member initializers

Give members defaults right where they're declared, as `total_qty_ = 0` above. Any constructor that doesn't
mention the member uses the default. This is the easiest way to avoid uninitialized members, the bug from the
start of Chapter 1.2.

### `explicit`

A constructor callable with one argument is also an **implicit conversion**:

```cpp
class OrderId { public: OrderId(std::uint64_t v) : v_(v) {} /* ... */ private: std::uint64_t v_; };
void cancel(OrderId id);
cancel(42);       // compiles: 42 silently becomes an OrderId. Was 42 an order ID, or a quantity?
```

Mark single-argument constructors `explicit` unless you specifically want the conversion:

```cpp
explicit OrderId(std::uint64_t v) : v_(v) {}
cancel(42);            // error
cancel(OrderId{42});   // OK, and the intent is visible
```

### Exercises

:::exercise Initialization order
What's wrong with this class, and what does `-Wall` say?

```cpp
class Window {
public:
    explicit Window(std::size_t n) : size_(n), data_(new double[size_]) {}
private:
    double*     data_;
    std::size_t size_;
};
```
:::solution
`data_` is declared before `size_`, so it's initialized first, using `size_`, which hasn't been initialized yet.
That reads an indeterminate value: undefined behaviour, and in practice an allocation of a random size. `-Wall`
gives `-Wreorder`: "`Window::size_` will be initialized after `Window::data_`". Fix it by declaring `size_` first,
or by using `n` instead of `size_` in the initializer. (Better still: use `std::vector<double>` and own nothing
manually.)
:::

---

## 3. Destructors and object lifetime

A **destructor**, `~ClassName()`, runs when an object's lifetime ends. It takes no arguments, returns nothing, and
should never throw. (Destructors are implicitly `noexcept`; if one throws, `std::terminate` is called.)

When exactly does a destructor run?

| Object | Destroyed when |
|---|---|
| Local (automatic) variable | Control leaves its enclosing scope, by any route: falling off the end, `return`, `break`, `goto`, or an exception propagating out |
| Member | The enclosing object's destructor finishes |
| Element of a container | It's erased, or the container is destroyed |
| Temporary | At the end of the full expression (the `;`), unless lifetime-extended by a reference |
| Static / global | After `main` returns, in reverse order of construction |
| Heap object (`new`) | When `delete` is called, and only then |

Within one scope, objects are destroyed in **reverse** order of construction. Within one object, the destructor
body runs first, then members are destroyed in reverse declaration order, then base classes.

```cpp
struct Tracer {
    const char* name;
    explicit Tracer(const char* n) : name(n) { std::printf("open %s\n", name); }
    ~Tracer() { std::printf("close %s\n", name); }
};

void f(bool fail) {
    Tracer a("a");
    Tracer b("b");
    if (fail) throw std::runtime_error("boom");
    Tracer c("c");
}   // normal path prints: close c, close b, close a
    // exception path prints: close b, close a (c was never constructed)
```

Reverse order is exactly right for resources: if `b` was acquired after `a`, it might depend on `a`, so it must be
released first.

### Exercises

:::exercise Trace it
What does this print?

```cpp
struct Book { Tracer bids{"bids"}; Tracer asks{"asks"}; ~Book() { std::puts("~Book"); } };
int main() { Tracer log("log"); Book book; }
```
:::solution
```text
open log
open bids
open asks
~Book
close asks
close bids
close log
```
Members are constructed in declaration order before the (implicit) constructor body. At the end of `main`, `book`
is destroyed first (declared last): its destructor body runs, then members in reverse order. Then `log`.
:::

---

## 4. RAII

Combine those guarantees and you get the pattern: **acquire in the constructor, release in the destructor.**

```cpp
class File {
public:
    explicit File(const char* path, const char* mode) : f_(std::fopen(path, mode)) {
        if (!f_) throw std::system_error(errno, std::generic_category(), path);
    }
    ~File() { std::fclose(f_); }

    File(const File&) = delete;              // one owner only: copying would double-close
    File& operator=(const File&) = delete;

    std::FILE* get() const { return f_; }
private:
    std::FILE* f_;
};
```

The invariant is "`f_` is an open file." The constructor establishes it or throws (so no half-built `File` ever
exists), and the destructor ends it. Copying is deleted because two `File` objects owning the same handle would
both close it. Chapter 2.3 shows how to make it *movable*.

Now the function from the introduction:

```cpp
void process_snapshot(const char* path) {
    File f(path, "rb");
    std::vector<char> buf(1 << 20);
    std::scoped_lock lock(book_mutex);

    if (!read_header(f.get(), buf.data())) return;   // everything released, in reverse order
    apply(buf.data());                               // if this throws: everything released
}
```

No cleanup code at all, and it's correct on every path. That's the whole idea.

### RAII in the standard library

You'll rarely write RAII classes from scratch, because the standard library is full of them:

| Resource | RAII type |
|---|---|
| Heap memory, one object | `std::unique_ptr<T>`, `std::shared_ptr<T>` |
| Heap memory, many objects | `std::vector<T>`, `std::string`, every container |
| Mutex lock | `std::lock_guard`, `std::scoped_lock`, `std::unique_lock` |
| Thread | `std::jthread` (joins in its destructor) |
| File stream | `std::ifstream`, `std::ofstream` |

When you meet a C API that hands you a handle and a `close` function (a socket, an `mmap`, an `epoll` fd, a
`pthread_attr_t`), wrap it immediately.

### Scope guards

For one-off cleanup that doesn't deserve a class, a **scope guard** runs a callable on scope exit:

```cpp
template <class F>
class ScopeGuard {
public:
    explicit ScopeGuard(F f) : f_(std::move(f)) {}
    ~ScopeGuard() { if (active_) f_(); }
    void dismiss() { active_ = false; }
    ScopeGuard(const ScopeGuard&) = delete;
    ScopeGuard& operator=(const ScopeGuard&) = delete;
private:
    F f_;
    bool active_ = true;
};

void reserve_then_send(Risk& risk, Gateway& gw, const Order& o) {
    risk.reserve(o);
    ScopeGuard undo([&] { risk.release(o); });   // roll back if anything below fails
    gw.send(o);                                  // may throw
    undo.dismiss();                              // success: keep the reservation
}
```

This "commit or roll back" shape comes up constantly in order handling. The practice set has you build a full
scope guard.

> [!PERF]
> RAII is free. A destructor call is just a function call the compiler would have had to emit anyway for manual
> cleanup, and simple destructors are inlined. The exception-handling paths live in separate **cold** code
> sections with tables, and cost nothing unless an exception is actually thrown.

### Exercises

:::exercise Wrap a file descriptor
Sketch an RAII class `Fd` for a POSIX file descriptor (`int`, closed with `::close`, `-1` means "none"). What
should the destructor do if the fd is `-1`? Should `Fd` be copyable?
:::solution
```cpp
class Fd {
public:
    explicit Fd(int fd) : fd_(fd) {}
    ~Fd() { if (fd_ >= 0) ::close(fd_); }
    Fd(const Fd&) = delete;
    Fd& operator=(const Fd&) = delete;
    int get() const { return fd_; }
private:
    int fd_ = -1;
};
```
The destructor must skip `close` when there's nothing to close. It shouldn't be copyable: two copies would close
the same descriptor twice, and after the first close the number may already have been reused for a different file,
so the second close would close *someone else's* descriptor. It should be movable (Chapter 2.3), leaving the source
at `-1`.
:::

:::exercise Where does the guard go?
In `reserve_then_send`, why is the `ScopeGuard` created *after* `risk.reserve(o)`, rather than before it?
:::solution
If `reserve` itself throws, nothing was reserved, so there's nothing to release. Creating the guard first would
call `release` for a reservation that never happened. The rule is the same as RAII generally: the guard takes
responsibility for a resource only once that resource has actually been acquired.
:::

---

## 5. Constructors that fail

What should a constructor do when it can't establish the invariant? It has no return value. The options:

1. **Throw an exception.** The idiomatic C++ answer: the object never exists, and any members already constructed
   are destroyed automatically.
2. **A factory function** returning `std::optional<T>` or `std::expected<T, E>`, with a private constructor that
   can't fail. Common in code built without exceptions or on hot paths (Chapter 2.7).
3. **A two-phase "init()" method** with an `is_valid()` flag. Avoid this: it allows objects that exist but aren't
   usable, so every member function has to check.

```cpp
class Instrument {
public:
    static std::optional<Instrument> create(std::string symbol, std::int64_t tick) {
        if (tick <= 0 || symbol.empty()) return std::nullopt;
        return Instrument(std::move(symbol), tick);
    }
private:
    Instrument(std::string s, std::int64_t t) : symbol_(std::move(s)), tick_(t) {}
    std::string symbol_;
    std::int64_t tick_;
};
```

> [!IMPORTANT]
> If a constructor throws, the **destructor does not run** for that object, since it never fully existed. Only
> the members and bases that were already constructed are destroyed. So a constructor that acquires two raw
> resources and throws after the first leaks it. The fix is the same as always: make each resource its own RAII
> member, so it's cleaned up as a fully constructed subobject.

---

## Summary

- Use `struct` for plain data and `class` when there's an invariant to protect. Encapsulation costs nothing.
- Initialize members in the initializer list or with default member initializers; members are initialized in
  declaration order.
- Mark single-argument constructors `explicit`.
- Destructors run deterministically: at scope exit on every path, in reverse order of construction.
- **RAII:** acquire in the constructor, release in the destructor. It makes cleanup automatic, exception-safe and
  free.
- Wrap every C handle immediately, and use scope guards for one-off commit-or-rollback logic.
- A constructor that can't establish its invariant should throw, or be replaced by a factory returning
  `optional`/`expected`.

## Key terms

**Invariant** · **encapsulation** · **member initializer list** · **default member initializer** ·
**`explicit`** · **destructor** · **RAII** · **scope guard** · **factory function**
