+++
title = "Values, References and Pointers"
minutes = 40
practice_tags = ["memory", "raii", "move-semantics"]
+++

## Why this chapter matters

Three bugs from real trading code:

- A function `void update(Book book)` applies a fill to the book it was given. The caller's book never changes,
  because the function received a **copy** of a 2 MB object, edited the copy, and threw it away. It was also
  slow: every call copied 2 MB.
- A strategy keeps `const Order* best = &orders[0];` and then calls `orders.push_back(...)`. The vector
  reallocates, `best` now points into freed memory, and the next read returns garbage that *looks* like a price.
- Two teams argue for a week about who should `delete` a `MarketDataHandler*`. Neither does, and the process
  slowly leaks. Then someone adds a `delete` in both places, and it crashes.

All three come from not being precise about one question: **when I write this name, am I talking about an
object, or about a way to reach an object somewhere else?** C++ gives you three answers (values, references and
pointers) and expects you to pick deliberately. This chapter makes the choice mechanical.

By the end of this chapter you should be able to:

- say exactly what is copied when you pass or return by value;
- choose between `T`, `const T&`, `T&` and `T*` for a parameter, and justify it;
- use `const` correctly, including on pointers and member functions;
- do pointer arithmetic within an array, and know where it becomes undefined;
- describe ownership with the right vocabulary: owning, non-owning, borrowed, dangling.

---

## 1. Values: every variable is an object

In many languages a variable of class type is secretly a reference to a heap object. In C++ it is not. A variable
**is** the object, stored right there: on the stack for a local, inline inside the enclosing object for a member.

```cpp
struct Quote {
    std::int64_t bid, ask;   // integer ticks
    std::int32_t bid_qty, ask_qty;
};

Quote a{10000, 10001, 500, 300};
Quote b = a;      // b is a second, independent Quote: 24 bytes copied
b.bid = 9999;     // a.bid is still 10000
```

Assignment and initialization from another object **copy** it. For a `Quote`, that's 24 bytes and costs about as
much as copying three integers, which is to say nothing. For a `std::vector<double>` with a million elements,
copying allocates 8 MB and copies every element.

Passing by value works the same way: the parameter is a new object, initialized from the argument.

```cpp
void widen(Quote q) { q.bid -= 1; q.ask += 1; }   // modifies the local copy only

Quote q{10000, 10001, 500, 300};
widen(q);          // q is unchanged
```

Value semantics are a feature, not a limitation. When you hold a value, nobody else can change it behind your
back, and you never have to ask who owns it. Copies of small objects are extremely cheap, and they're often
*faster* than indirection because the data is right there in registers or in the same cache line.

> [!NOTE]
> "Small" in practice means up to about 16 bytes of trivially copyable data. On x86-64 Linux, a struct of up to
> two 8-byte fields is passed in **registers**, not memory. Larger structs are passed via a hidden pointer to a
> copy on the stack (Chapter 2.1 covers the calling convention).

### Exercises

:::exercise What gets copied?
How many `Quote` objects exist at the end of this code, and what is `quotes[0].bid`?

```cpp
std::vector<Quote> quotes;
Quote q{100, 101, 1, 1};
quotes.push_back(q);
q.bid = 99;
Quote r = quotes[0];
r.bid = 98;
```
:::solution
Three: `q`, the copy inside the vector, and `r`. `push_back(q)` copies `q` into the vector's storage, and
`Quote r = quotes[0]` makes another copy. Neither later assignment touches the vector, so `quotes[0].bid` is
still `100`.
:::

---

## 2. References: another name for an existing object

A **reference** is an alias: a second name for an object that already exists.

```cpp
Quote q{10000, 10001, 500, 300};
Quote& r = q;     // r *is* q
r.bid = 9999;     // q.bid is now 9999
```

Three rules make references simple:

1. A reference must be initialized when it's declared, and it must refer to an object.
2. It can never be re-seated. `r = other;` assigns *through* `r` into `q`; it doesn't make `r` refer to `other`.
3. There is no "null reference" in a correct program.

The main use of references is in function parameters:

```cpp
void widen(Quote& q) { q.bid -= 1; q.ask += 1; }   // modifies the caller's object

double mid(const Quote& q) {                       // reads it without copying
    return (q.bid + q.ask) / 2.0;
}
```

Under the hood the compiler passes an address, just like a pointer. The difference is entirely in what the
language lets you do with it: no null, no arithmetic, no re-seating. Those restrictions are exactly what make
references the right default for "I need to look at (or change) your object, but I don't own it."

### `const` references and temporaries

A `const T&` can bind to a temporary, and the temporary's lifetime is extended to match the reference:

```cpp
const std::string& s = std::string("AAPL") + ".O";   // OK: temporary lives as long as s
std::string& t = std::string("AAPL");                // error: non-const lvalue ref to a temporary
```

This is why `const T&` parameters accept anything: named objects, temporaries and implicit conversions alike.

> [!WARNING]
> Lifetime extension applies only to a reference bound **directly** to the temporary. It does not chain through
> function returns. `const std::string& sym = make_order().symbol;` extends the whole temporary `Order`, but
> `const std::string& sym = get_symbol(make_order());` (where `get_symbol` returns a `const std::string&` into
> its argument) dangles at the end of the statement.

### Exercises

:::exercise Re-seating
What does this print?

```cpp
int a = 1, b = 2;
int& r = a;
r = b;
b = 3;
std::printf("%d %d %d\n", a, b, r);
```
:::solution
`2 3 2`. `r = b` doesn't re-seat `r`; it assigns `b`'s value (2) into `a`, because `r` *is* `a`. Changing `b`
afterwards doesn't affect `a` or `r`.
:::

---

## 3. Choosing a parameter type

Most parameter-passing decisions come down to this table:

| You need to... | Use | Example |
|---|---|---|
| read a cheap-to-copy value | `T` | `int64_t qty`, `Side side`, `Price p`, `std::string_view sym` |
| read an expensive object | `const T&` | `const Book& book`, `const std::vector<Fill>& fills` |
| modify the caller's object | `T&` | `Book& book` |
| take ownership of / keep a copy of a value | `T` (then `std::move` it into place) | `void set_name(std::string name)` |
| accept "maybe no object" | `T*` (or `std::optional<T>` for values) | `const Order* find(OrderId id)` |

"Cheap to copy" means roughly: fundamental types, enums, small trivially copyable structs (≤ 16 bytes), and
non-owning views like `std::string_view` and `std::span`. Everything else (strings, vectors, maps, anything that
owns heap memory) goes by `const&` when you only read it.

Why not pass *everything* by `const&`? Two reasons:

1. **Indirection cost.** A `const int&` forces the value into memory so it has an address, and every read is a
   load through a pointer.
2. **Aliasing.** The compiler must assume a reference might alias other memory you write to, which blocks
   optimizations. With a value, it knows nobody else can change it.

```cpp
void scale_all(std::vector<double>& v, const double& factor) {
    for (double& x : v) x *= factor;   // must reload factor every iteration:
}                                       // what if factor refers to an element of v?
```

Passing `factor` by value removes the question entirely. We return to aliasing properly in Chapter 8.2.

> [!TIP]
> **Output parameters** (`void compute(Result& out)`) are an older style. Since C++17 guarantees copy elision
> for returned temporaries (Chapter 2.4), prefer returning the result: `Result compute()`. The exception is
> reusing a buffer on a hot path, where filling a caller-owned `std::vector&` avoids a fresh allocation each call.

### Exercises

:::exercise Pick the signature
Choose a parameter type for each, and justify it:
(a) `notional(price, qty)` with `int64_t` ticks and quantity;
(b) `apply_fill(book, fill)`, which modifies the book; `Fill` is a 32-byte struct;
(c) `log_symbol(symbol)`, which only prints it;
(d) `Strategy::set_config(config)`, which stores a `Config` containing several `std::string`s.
:::solution
(a) `int64_t notional(int64_t price, int64_t qty)`: fundamental types by value.
(b) `void apply_fill(Book& book, const Fill& fill)`: the book is modified so it's `T&`; a 32-byte `Fill` is
past the register-passing threshold, so `const&` (by value would also be defensible).
(c) `void log_symbol(std::string_view symbol)`: a view by value accepts `std::string`, string literals and
substrings without copying.
(d) `void set_config(Config config) { config_ = std::move(config); }`: callers passing a temporary pay one move;
callers passing an lvalue pay exactly one copy, which they'd have needed anyway.
:::

---

## 4. `const`, precisely

`const` is a promise checked by the compiler: "this won't be modified through this name." Use it everywhere it's
true. It documents intent, catches bugs, and lets functions accept temporaries.

For pointers, `const` applies to whatever is on its **left** (or to the right if nothing is on the left):

```cpp
const Order* p1;        // pointer to const Order: can't modify *p1, can re-point p1
Order const* p2;        // same thing, written differently
Order* const p3 = &o;   // const pointer to Order: can modify *p3, can't re-point p3
const Order* const p4 = &o;   // neither
```

Read declarations right to left: `Order* const p3` is "p3 is a const pointer to Order."

### `const` member functions

A member function marked `const` promises not to modify the object. Only `const` members can be called through
a `const` reference or pointer:

```cpp
class Book {
public:
    Price best_bid() const { return bids_.empty() ? 0 : bids_.front().price; }
    void  add(const Order& o);                    // non-const: modifies the book
private:
    std::vector<Level> bids_;
};

void print_top(const Book& b) {
    b.best_bid();   // OK
    b.add({});      // error: add is not const
}
```

Forgetting `const` on a getter is a common bug in new C++ code: it makes the class unusable through `const&`,
which pushes callers into copying or casting.

`mutable` members can be changed even in a `const` function. Use it only for state that's invisible from the
outside, like a cache or a mutex.

> [!NOTE]
> `const` on a local or parameter rarely helps the optimizer: it can see whether you modify a local anyway. Its
> value is correctness. `const` on an object *definition* (e.g. a global table) does let the compiler place it in
> read-only memory and fold its values into code.

### Exercises

:::exercise Read the declaration
Describe each type in words: (a) `const char* const* p`; (b) `int* const& r`; (c) `const std::vector<int>* v`.
:::solution
(a) `p` is a pointer to a const pointer to const char: you can re-point `p`, but not change the pointer it points
to, nor the characters. (b) `r` is a reference to a const pointer to int: you can modify the int through it, but
not re-point the pointer. (c) `v` is a pointer to a const vector of int: you can re-point `v` and read the vector,
but not modify it through `v`.
:::

---

## 5. Pointers: addresses as values

A **pointer** is an object that holds an address. Unlike a reference it can be null, re-pointed, and stored in
containers, and it supports arithmetic.

```cpp
Quote q{10000, 10001, 500, 300};
Quote* p = &q;          // & takes the address
p->bid = 9999;          // -> is (*p).bid
Quote* none = nullptr;  // points to nothing
if (none) { /* never taken */ }
```

Use `nullptr`, never `0` or `NULL`. `nullptr` has its own type, so it can't be mistaken for an integer in
overload resolution.

### Pointer arithmetic

Adding `n` to a `T*` advances by `n * sizeof(T)` bytes. It's only defined **within a single array** (including
one-past-the-end):

```cpp
std::int64_t prices[5] = {100, 101, 102, 103, 104};
std::int64_t* first = prices;        // array decays to pointer to element 0
std::int64_t* last  = prices + 5;    // one past the end: valid to form and compare, NOT to dereference

for (std::int64_t* it = first; it != last; ++it)
    std::printf("%lld\n", static_cast<long long>(*it));

std::ptrdiff_t n = last - first;     // 5 elements (not 40 bytes)
```

```layout "prices + k advances k elements, i.e. 8k bytes. prices + 5 is a valid 'one past the end' pointer; dereferencing it is undefined."
title: std::int64_t prices[5]
row: 40
prices[0]:8:1 prices[1]:8:1 prices[2]:8:1 prices[3]:8:1 prices[4]:8:1
```

This is exactly the iterator model the STL is built on (Chapter 4.3): a half-open range `[first, last)`.

Forming a pointer outside `[first, last]`, even without dereferencing it, is undefined behaviour. So is
subtracting pointers into two different arrays, or comparing them with `<`.

### Arrays decay

A C array passed to a function **decays** to a pointer to its first element, and the size is lost:

```cpp
void bad(std::int64_t prices[5]) {        // really: std::int64_t* prices
    sizeof(prices);                       // 8, the size of a pointer!
}
```

In modern C++, pass a `std::span<const std::int64_t>` instead (Chapter 2.6). It's a pointer and a length,
passed by value in two registers.

### Exercises

:::exercise Is it UB?
Which of these are undefined behaviour, given `int a[4];`?
(a) `int* p = a + 4;` (b) `int x = *(a + 4);` (c) `int* q = a + 5;` (d) `int* r = a - 1;` (e) `bool b = (a + 4) > a;`
:::solution
(a) Fine: one past the end is a valid pointer value. (b) UB: dereferencing one past the end. (c) UB: forming a
pointer more than one past the end. (d) UB: forming a pointer before the beginning (a common bug in reverse
loops). (e) Fine: both pointers are within `[a, a+4]`, so `>` is defined and true.
:::

:::exercise The reverse loop
This loop is meant to walk an array backwards. What's wrong with it, and how do you fix it?

```cpp
for (const int* p = a + n - 1; p >= a; --p) use(*p);
```
:::solution
After processing `a[0]`, `--p` forms `a - 1`, which is undefined, and then compares it. In practice it usually
"works", but it's UB and sanitizers or optimizers can break it. Fix: iterate from the end and decrement *before*
use: `for (const int* p = a + n; p != a; ) use(*--p);`, or use `std::views::reverse` / reverse iterators.
:::

---

## 6. Dangling: when the object is gone

A reference or pointer is only valid while its object is alive. When the object dies first, it **dangles**, and
using it is undefined behaviour. The three classic sources:

**Returning a reference to a local.**

```cpp
const std::string& symbol_for(int id) {
    std::string s = lookup(id);
    return s;               // s is destroyed at return; the caller gets a dangling reference
}
```

GCC and Clang warn about this one (`-Wreturn-local-addr`). Return by value instead.

**Iterator and pointer invalidation.** Operations that reallocate a container invalidate pointers into it:

```cpp
std::vector<Order> orders = load();
const Order* best = &orders[0];
orders.push_back(next);       // may reallocate: best now dangles
use(best->price);             // UB
```

Chapter 4.2 lists exactly which operations invalidate what. The robust fix in long-lived structures is to store
**indices** instead of pointers; an index stays valid across reallocation.

**Views outliving their data.**

```cpp
std::string_view sym = std::string("MSFT");   // the temporary string dies at the semicolon
std::printf("%.*s\n", int(sym.size()), sym.data());   // UB
```

`string_view` and `span` are non-owning by design, so they are exactly as dangerous as raw pointers here.

> [!TIP]
> ASan catches most dangling accesses at run time (`heap-use-after-free`, `stack-use-after-return`). Another
> reason the practice problems always run with it on.

### Exercises

:::exercise Find the dangle
Is there a dangling reference here? If so, where?

```cpp
struct Config { std::string venue; };
Config load_config();

const std::string& venue = load_config().venue;
std::printf("%s\n", venue.c_str());
```
:::solution
No. `load_config()` returns a temporary `Config`, and binding a `const&` directly to a member subobject of a
temporary extends the lifetime of the **whole** temporary. This is subtle enough that many style guides ban it
anyway: `const Config cfg = load_config();` followed by `cfg.venue` is clearer and costs nothing thanks to copy
elision.
:::

---

## 7. Ownership vocabulary

Every heap object needs exactly one answer to "who destroys this?" The vocabulary:

| Term | Meaning | Spelled in modern C++ |
|---|---|---|
| **Owning** | responsible for destroying the object | a value member, `std::unique_ptr<T>`, `std::vector<T>`, `std::shared_ptr<T>` (shared) |
| **Non-owning / borrowed** | may use the object; someone else destroys it | `T&`, `const T&`, `T*`, `std::span`, `std::string_view` |
| **Dangling** | refers to an object that's already been destroyed | a bug |

The modern rule, which we'll build on in Chapters 2.2 and 4.6:

> **Raw pointers and references never own.** Ownership is always expressed by a value or a smart pointer.

So a `T*` in a modern interface means "here's an object you may look at; it might be null; don't delete it." When
you see `new` and `delete` in application code, something is usually wrong. The exceptions are inside the
implementation of containers, allocators and smart pointers.

```cpp
class Strategy {
public:
    explicit Strategy(const RiskLimits& limits) : limits_(&limits) {}   // borrows: limits must outlive us
    void on_fill(const Fill& f);
private:
    const RiskLimits* limits_;            // non-owning
    std::vector<Order> working_orders_;   // owning, by value
    std::unique_ptr<Model> model_;        // owning, on the heap (e.g. polymorphic)
};
```

> [!NOTE]
> Why store `limits_` as a pointer rather than a `const RiskLimits&` member? A reference member makes the class
> non-assignable (references can't be re-seated), which is usually more annoying than useful. A pointer member
> with a reference constructor parameter documents "not null" at the boundary and keeps the class assignable.

### Exercises

:::exercise Who owns it?
For each member, say whether it's owning or non-owning, and what must be true for the class to be correct:
`std::vector<Level> levels_;`, `const Instrument* instrument_;`, `std::unique_ptr<Feed> feed_;`,
`std::string_view venue_;`.
:::solution
`levels_`: owning, by value; nothing extra required. `instrument_`: non-owning; the `Instrument` must outlive
this object. `feed_`: owning, on the heap; destroyed automatically with the object, and the class becomes
move-only. `venue_`: non-owning; the characters it points at must outlive this object. A `string_view` member
is a common source of dangling, e.g. if it was constructed from a temporary `std::string`. Prefer
`std::string` unless you control the storage's lifetime.
:::

---

## Summary

- A C++ variable **is** its object. Assignment and pass-by-value **copy**, which is cheap for small types and
  potentially very expensive for owning ones.
- A reference is an alias: never null, never re-seated. `const T&` binds to anything, including temporaries.
- Default parameter choices: small types by value, large types by `const&`, modified objects by `&`, optional
  objects by pointer, sink arguments by value plus `std::move`.
- Put `const` everywhere it's true, especially on member functions that don't modify the object.
- Pointer arithmetic is only valid inside one array, plus one past the end.
- References, pointers and views dangle when their object dies first. Container reallocation is the most common
  cause in practice; indices survive it.
- Raw pointers and references never own. Ownership lives in values and smart pointers.

## Key terms

**Value semantics** · **reference** · **pointer** · **const-correctness** · **pointer arithmetic** ·
**array decay** · **dangling** · **lifetime extension** · **owning** · **non-owning** · **invalidation**
