+++
title = "Value Categories and Move Semantics"
minutes = 45
practice_tags = ["move-semantics", "templates"]
+++

## Why this chapter matters

A reference-data loader builds a `std::vector<Instrument>` of 50,000 instruments, each with a few strings, and
returns it. Before C++11, returning it might deep-copy every string. Storing it into a member copied it again.
Inserting each `Instrument` into a map copied it again. Most of the program's start-up time was spent duplicating
data that was about to be thrown away.

**Move semantics** fix this. When the source of a copy is about to die anyway, C++ lets you *steal* its resources
instead: three pointer copies instead of a heap allocation and a `memcpy`. But to use it well, and to avoid the
mistakes that make code silently slower, you need to understand what the compiler means by an *rvalue*, what
`std::move` actually does (nothing!), and when the compiler skips the copy or move entirely.

By the end of this chapter you should be able to:

- classify an expression as an lvalue, prvalue or xvalue;
- explain what `std::move` does and when using it helps, hurts, or is a bug;
- write functions that take ownership of arguments efficiently;
- explain guaranteed copy elision and named return value optimization;
- write a perfectly forwarding function template with `std::forward`.

---

## 1. Value categories

Every expression in C++ has a type *and* a **value category**. The category answers: does this expression name an
object that will still exist afterwards?

- An **lvalue** names an object with an identity that persists: a variable, `*p`, `v[i]`, a function returning
  `T&`. You can take its address. (Historically: it can appear on the *left* of `=`.)
- A **prvalue** ("pure rvalue") is a value not yet tied to an object: a literal like `42`, `a + b`, a function
  call returning `T` by value, `Order{...}`.
- An **xvalue** ("expiring value") names an object whose resources may be reused: the result of `std::move(x)`,
  or a function returning `T&&`.

prvalues and xvalues together are **rvalues**: "nobody else cares about this object after this expression."

```cpp
std::string s = "AAPL";
std::string f();

s;              // lvalue
f();            // prvalue
s + ".O";       // prvalue
std::move(s);   // xvalue
```

> [!IMPORTANT]
> A **named** variable is always an lvalue, *even if its type is an rvalue reference*. Inside
> `void g(std::string&& x)`, the expression `x` is an lvalue. It has a name, so it could be used again on the
> next line, and the compiler won't move from it implicitly. This single rule explains most move-semantics
> confusion.

### Rvalue references

`T&&` is an **rvalue reference**: a reference that binds only to rvalues.

```cpp
void take(std::string& s);        // binds to lvalues
void take(const std::string& s);  // binds to anything
void take(std::string&& s);       // binds to rvalues only: callers have promised they won't use it again
```

With overloads like these, the compiler picks the `&&` version for temporaries and `std::move`d values, and the
`&` versions otherwise. That's how a move constructor `T(T&&)` gets selected over a copy constructor
`T(const T&)` precisely when the source is expendable.

### Exercises

:::exercise Classify
Classify each expression as lvalue, prvalue or xvalue: (a) `orders[3]`; (b) `orders.size()`; (c) `++i`;
(d) `i++`; (e) `std::move(orders)[3]`; (f) `"AAPL"`.
:::solution
(a) lvalue: `operator[]` returns `T&`. (b) prvalue: returns `size_t` by value. (c) lvalue: pre-increment returns
a reference to `i`. (d) prvalue: post-increment returns the old value by value. (e) For `std::vector`, `operator[]`
isn't ref-qualified, so it returns `T&` even on an rvalue vector: lvalue. (f) A string literal is an **lvalue**
of type `const char[5]`: it's an array object with static storage, the one literal that isn't a prvalue.
:::

---

## 2. `std::move` is a cast

`std::move` doesn't move anything. It's an unconditional cast to an rvalue reference:

```cpp
template <class T>
constexpr std::remove_reference_t<T>&& move(T&& t) noexcept {
    return static_cast<std::remove_reference_t<T>&&>(t);
}
```

All it does is change an lvalue into an xvalue, so that overload resolution will pick a move constructor or move
assignment. Whether anything is actually moved depends on the type. A `std::string` steals its buffer; an `int` or
a `Quote` struct of integers just copies, because for those there's nothing to steal.

```cpp
std::string a = "a long symbol name that won't fit in SSO";
std::string b = std::move(a);   // b steals a's heap buffer; a is now valid but unspecified (in practice empty)
```

### Where `std::move` helps

When you're done with a named object and want to hand its contents to someone else:

```cpp
class Instrument {
public:
    explicit Instrument(std::string symbol) : symbol_(std::move(symbol)) {}   // "sink" parameter
private:
    std::string symbol_;
};

std::vector<Instrument> instruments;
std::string sym = read_symbol();
instruments.emplace_back(std::move(sym));   // sym is not used again
```

The **sink parameter** idiom: take the parameter by value, then move it into place. A caller passing a temporary
pays one move; a caller passing an lvalue pays one copy (which they'd need anyway, since they're keeping their own)
plus one move. Moves of strings and vectors are a few instructions, so this is nearly optimal with one overload
instead of two.

### Where `std::move` hurts

**Moving a `const` object** silently copies. `std::move(const_str)` produces `const std::string&&`, which can't
bind to `std::string&&`, so the copy constructor is selected. No warning.

```cpp
const std::string sym = "AAPL";
std::string other = std::move(sym);   // copies
```

**`return std::move(local);`** is a pessimization:

```cpp
std::vector<double> load() {
    std::vector<double> v = read_all();
    return std::move(v);   // prevents NRVO (section 3); at best a move, instead of nothing at all
}
```

GCC warns about this with `-Wpessimizing-move`. Just write `return v;`. (Since C++11, returning a local by name
automatically treats it as an rvalue if elision doesn't happen, so `std::move` adds nothing.)

**Using an object after moving from it** is a logic bug, even though it's not undefined behaviour for standard
types. Clang-tidy's `bugprone-use-after-move` check finds it.

### Exercises

:::exercise Does it move?
In each case, does the vector's buffer get moved or copied?

```cpp
std::vector<int> make();
void consume(std::vector<int> v);

std::vector<int> a = make();     // (1)
consume(a);                       // (2)
consume(std::move(a));            // (3)
const std::vector<int> c = make();
consume(std::move(c));            // (4)
```
:::solution
(1) Neither: guaranteed copy elision constructs `a` directly from `make()`'s return value (section 3).
(2) Copied: `a` is an lvalue. (3) Moved: `std::move(a)` is an xvalue. (4) **Copied**: `c` is `const`, so
`std::move(c)` is `const std::vector<int>&&`, which only the copy constructor accepts.
:::

---

## 3. Copy elision: better than moving

Moving is cheap. Not constructing the extra object at all is cheaper. C++ has two forms of **copy elision**:

**Guaranteed elision (C++17)** for prvalues. When an object is initialized from a prvalue of the same type, no
temporary is created; the object is constructed directly in its final location:

```cpp
Order make_order() { return Order{42, Side::Buy, 100, 10050}; }

Order o = make_order();   // exactly one Order constructed, directly in o. No copy, no move.
```

This is guaranteed by the language: it works even if `Order` has no copy or move constructor at all. It's how
factory functions can return non-movable types.

**Named Return Value Optimization (NRVO)** for named locals. When a function returns a local variable by name, the
compiler may construct that local directly in the caller's return slot:

```cpp
std::vector<double> load(std::size_t n) {
    std::vector<double> v;
    v.reserve(n);
    for (std::size_t i = 0; i < n; ++i) v.push_back(read_price());
    return v;              // NRVO: v *is* the caller's object; nothing is copied or moved
}
```

NRVO isn't guaranteed, but GCC and Clang do it reliably when every return statement returns the same named
object. It's defeated by returning different locals on different paths, by returning a function parameter, or by
`return std::move(v)`. When NRVO doesn't happen, the implicit move kicks in, so you still never get a copy.

> [!TIP]
> This is why modern C++ prefers **returning values** over output parameters. `std::vector<Fill> fills =
> parse(msg);` is both clearer and at least as fast as `parse(msg, fills_out);`, except when you want to reuse
> an existing buffer's capacity across calls.

### Exercises

:::exercise Count the constructions
Instrument a type with printing constructors, then count how many constructions, copies and moves happen in
`Order o = make_order();` for each version of `make_order` below, compiled at `-O0` and `-O2`:

```cpp
Order v1() { return Order{1}; }
Order v2() { Order o{1}; return o; }
Order v3(bool b) { Order x{1}, y{2}; return b ? x : y; }
```
:::solution
`v1`: one construction, no copies or moves, at any optimization level: guaranteed elision. `v2`: one construction
at both `-O0` and `-O2` with GCC and Clang: NRVO is done even without optimization (it's a front-end transform).
`v3`: two constructions plus a **copy**: `b ? x : y` is an lvalue expression, not a plain name, so neither NRVO nor
the implicit move applies. Writing `if (b) return x; return y;` gets you a move instead.
:::

---

## 4. Forwarding references and `std::forward`

Inside a template, `T&&` with `T` deduced means something different: a **forwarding reference** (sometimes called
a "universal reference"). It binds to *anything*, and `T` records the argument's value category:

```cpp
template <class T>
void wrapper(T&& arg);

std::string s;
wrapper(s);              // T = std::string&   ; arg is std::string&   (reference collapsing: & && -> &)
wrapper(std::string{});  // T = std::string    ; arg is std::string&&
```

The reference-collapsing rule: any `&` wins. `& &`, `& &&` and `&& &` all collapse to `&`; only `&& &&` gives `&&`.

This lets a wrapper pass arguments through to another function **with their value category intact**, which is
called **perfect forwarding**:

```cpp
template <class... Args>
Order& OrderPool::create(Args&&... args) {
    Order* slot = allocate_slot();
    return *new (slot) Order(std::forward<Args>(args)...);   // construct in place
}
```

`std::forward<T>(arg)` is a *conditional* cast: it casts to an rvalue if `T` is a non-reference type (the caller
passed an rvalue), and leaves it as an lvalue otherwise. Without it, `arg` is always an lvalue (it has a name), and
every argument would be copied.

This is exactly what `emplace_back`, `std::make_unique` and `std::make_shared` do. It's why
`v.emplace_back(sym, qty)` constructs the element directly inside the vector with no temporary.

| Write | When |
|---|---|
| `std::move(x)` | `x` is a non-template rvalue reference, or any object you're finished with |
| `std::forward<T>(x)` | `x` is a forwarding reference `T&&` where `T` is a deduced template parameter |

> [!WARNING]
> `T&&` is a forwarding reference only when `T` is being deduced for *that function call*. In
> `template <class T> class Box { void set(T&& v); };`, `T` is already fixed by the class, so `set` takes a plain
> rvalue reference and won't accept lvalues. And `const T&&` is never a forwarding reference.

### Exercises

:::exercise Why forward?
`template <class T> void push(std::vector<std::string>& v, T&& s) { v.push_back(s); }`. What's suboptimal, and how
do you fix it?
:::solution
`s` is a named variable, so it's an lvalue, and `push_back(s)` always **copies**, even when the caller passed a
temporary or `std::move`d string. Fix: `v.push_back(std::forward<T>(s));`, which moves when the caller passed an
rvalue and copies when they passed an lvalue. Or use `v.emplace_back(std::forward<T>(s));`.
:::

---

## 5. Putting it together: the cost model

| Operation on `std::string` (long) or `std::vector` | Cost |
|---|---|
| Copy | heap allocation plus copying every element: O(n), hundreds of ns or more |
| Move | copy three pointers, null out the source: a few ns |
| Elided (RVO/NRVO, guaranteed elision) | free |

So, in order of preference:

1. **Elide.** Return by value, initialize from prvalues, use `emplace_back` to construct in place.
2. **Move.** When an object is handed off and won't be used again, `std::move` it. Mark your move operations
   `noexcept` (Chapter 2.3).
3. **Copy** only when you genuinely need two independent objects.

> [!PERF]
> Moves aren't free for every type. Moving a `std::array<double, 1000>` copies 8 KB, because the data is inline,
> not on the heap. So does moving a struct of a dozen `double`s. Moves are only cheap when the object holds its
> data *indirectly*. On the hot path, the best case is often that neither copy nor move happens: pass by reference,
> or construct in place.

---

## Summary

- Every expression is an **lvalue** (has an identity that persists), a **prvalue** (a pure value) or an
  **xvalue** (an expiring object). Rvalues are prvalues plus xvalues.
- A named variable is an lvalue even if its declared type is `T&&`.
- `std::move` is just a cast to rvalue. Moving `const` objects silently copies; `return std::move(local)` defeats
  NRVO.
- Take ownership with the sink idiom: parameter by value, then `std::move` it into place.
- C++17 guarantees elision when initializing from a prvalue. NRVO usually elides returned locals.
- In templates, `T&&` with deduced `T` is a forwarding reference; pass it on with `std::forward<T>`.
- Moves are only cheap for types that hold data indirectly.

## Key terms

**Value category** · **lvalue** · **prvalue** · **xvalue** · **rvalue reference** · **`std::move`** ·
**sink parameter** · **copy elision** · **RVO / NRVO** · **forwarding reference** · **reference collapsing** ·
**perfect forwarding** · **`std::forward`**
