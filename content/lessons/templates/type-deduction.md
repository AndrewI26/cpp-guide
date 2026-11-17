+++
title = "Type Deduction and auto"
minutes = 35
practice_tags = ["templates", "move-semantics"]
+++

## Why this chapter matters

```cpp
const std::vector<Order>& orders = book.orders();
for (auto o : orders) total += o.qty;            // copies every Order
auto& best = book.best_bid_level();              // fine... or a dangling reference?
auto px = 0;                                     // int, not int64_t or double
decltype(auto) get() { auto x = compute(); return (x); }   // returns a reference to a dead local
```

Each line compiles. Two are slow, one is subtly wrong, and one is undefined behaviour. All four come from
not knowing exactly what type the compiler deduces.

C++ deduces types in three places: template arguments, `auto` declarations, and `decltype`. They follow almost the
same rules, and those rules quietly strip references and `const`, collapse `&&`, and decay arrays. This chapter
lays them out so you can predict the type of every `auto` you write, which is essential for code that neither
copies by accident nor dangles.

By the end of this chapter you should be able to:

- predict the deduced type for template parameters taken by value, by reference, and by forwarding reference;
- apply the same rules to `auto`, `auto&`, `const auto&` and `auto&&`;
- explain the difference between `decltype(x)` and `decltype((x))`, and use `decltype(auto)` safely;
- choose the right form of `auto` in range-for loops and with structured bindings.

---

## 1. Template argument deduction: three cases

For `template <class T> void f(ParamType param);` called as `f(expr)`, the compiler deduces `T` (and so
`ParamType`) from the type of `expr`. There are three cases, depending on the form of `ParamType`.

### Case 1: by value, `f(T param)`

The argument is copied, so its top-level `const` and reference-ness don't matter and are **dropped**. Arrays and
functions **decay** to pointers.

```cpp
template <class T> void f(T param);

int x = 1; const int cx = x; const int& rx = x;
const char name[] = "AAPL";

f(x);      // T = int
f(cx);     // T = int        (top-level const dropped: param is a copy)
f(rx);     // T = int        (reference and const dropped)
f(name);   // T = const char* (array decays)
```

`const` that's *pointed to* is kept: for `const char* p`, `f(p)` deduces `T = const char*`. Only the copy's own
constness is irrelevant.

### Case 2: by reference, `f(T& param)` or `f(const T& param)`

References are dropped from the argument, then `T` is matched against what's left, **keeping** `const`:

```cpp
template <class T> void g(T& param);

g(x);      // T = int,        param: int&
g(cx);     // T = const int,  param: const int&   (const is part of the referenced object's type)
g(rx);     // T = const int,  param: const int&
g(name);   // T = const char[5], param: const char(&)[5]   (no decay: you get the array, with its size)
g(42);     // error: can't bind int& to a prvalue

template <class T> void h(const T& param);
h(x);      // T = int, param: const int&
h(42);     // T = int, param: const int&  (binds to the temporary)
```

The array case is useful: a reference parameter preserves the array's size, which is how `std::size(arr)` works.

### Case 3: forwarding reference, `f(T&& param)`

As in Chapter 2.4, `T&&` with deduced `T` is special. If the argument is an **lvalue**, `T` is deduced as an
**lvalue reference**, and reference collapsing turns `param` into an lvalue reference too. If it's an rvalue, `T`
is the plain type.

```cpp
template <class T> void fwd(T&& param);

fwd(x);              // lvalue: T = int&,        param: int&
fwd(cx);             // lvalue: T = const int&,  param: const int&
fwd(42);             // rvalue: T = int,         param: int&&
fwd(std::move(x));   // rvalue: T = int,         param: int&&
```

This is the only case where `T` itself becomes a reference type, and it's what makes `std::forward<T>` work: `T`
remembers whether the original was an lvalue.

### Exercises

:::exercise Deduce
For `template <class T> void f(T p); template <class T> void g(const T& p); template <class T> void h(T&& p);`,
what is `T` in each call? The argument is `const std::string s;`.
(a) `f(s)`; (b) `g(s)`; (c) `h(s)`; (d) `h(std::string{})`; (e) `f("AAPL")`; (f) `g("AAPL")`.
:::solution
(a) `std::string` (copy; const dropped). (b) `std::string` (the `const` is already in the parameter). (c)
`const std::string&` (lvalue to a forwarding reference). (d) `std::string`. (e) `const char*` (array decays when
taken by value). (f) `char[5]`, with `p` of type `const char(&)[5]` (no decay through a reference).
:::

---

## 2. `auto` follows the same rules

`auto` in a variable declaration uses template deduction, with `auto` playing the role of `T`:

| Declaration | Behaves like | Result for `const int& rx` |
|---|---|---|
| `auto v = expr;` | Case 1 (by value) | `int`: a copy, `const` dropped |
| `auto& v = expr;` | Case 2 | `const int&` |
| `const auto& v = expr;` | Case 2 | `const int&` |
| `auto&& v = expr;` | Case 3 (forwarding) | `const int&` (lvalue) |
| `auto* v = expr;` | Case 2 for pointers | requires a pointer |

So plain `auto` **always makes a copy** and never deduces a reference or top-level `const`.

```cpp
const std::vector<Order>& orders = book.orders();
auto a = orders;           // std::vector<Order>: a full copy of the vector!
const auto& b = orders;    // const std::vector<Order>&: no copy
```

The one place `auto` differs from templates: a braced initializer. `auto x = {1, 2, 3};` deduces
`std::initializer_list<int>`, while a template parameter can't be deduced from a braced list at all. And
`auto x{1};` deduces `int` (since C++17). Avoid `auto` with braces unless you mean `initializer_list`.

### Return type deduction

Functions can deduce their return type with `auto`, using Case 1 rules on the returned expression:

```cpp
auto mid(std::int64_t b, std::int64_t a) { return (b + a) / 2.0; }   // returns double

auto& level(Book& b, std::size_t i) { return b.levels[i]; }           // returns Level&
```

Plain `auto` returns by value, so a function returning `auto` never returns a reference, even if the expression
is one. To return a reference, say `auto&` or `const auto&` explicitly (or use `decltype(auto)`, section 3).

### Exercises

:::exercise Silent copies
Which lines copy the `Instrument` or vector?

```cpp
const std::vector<Instrument>& instruments = ref_data.instruments();
auto all = instruments;                       // (1)
auto& all_ref = instruments;                  // (2)
auto first = instruments.front();             // (3)
const auto& first_ref = instruments.front();  // (4)
auto&& any = instruments.front();             // (5)
```
:::solution
(1) copies the whole vector. (2) no copy: `const std::vector<Instrument>&`. (3) copies one `Instrument`. (4) no
copy. (5) no copy: `front()` on a const vector returns an lvalue `const Instrument&`, so `auto&&` deduces
`const Instrument&`.
:::

---

## 3. `decltype` and `decltype(auto)`

`decltype(expr)` gives the type of an expression, **without** the stripping that `auto` does. It has two modes:

- For an unparenthesized **name** (a variable or member), it gives that entity's *declared* type, exactly.
- For any other expression, it gives the type plus its value category: `T&` for an lvalue, `T&&` for an xvalue,
  `T` for a prvalue.

```cpp
int x = 0; const int& rx = x;
decltype(x)      // int        (declared type of x)
decltype(rx)     // const int& (declared type of rx)
decltype((x))    // int&       ((x) is an expression, and it's an lvalue)
decltype(x + 1)  // int        (prvalue)
decltype(std::move(x))   // int&&
```

The `decltype(x)` vs `decltype((x))` distinction is weird but real. It matters with `decltype(auto)`.

### `decltype(auto)`

`decltype(auto)` deduces a type using `decltype` rules instead of `auto` rules. Its main use is in wrappers that
must return exactly what the wrapped function returns, reference or not:

```cpp
template <class F, class... Args>
decltype(auto) timed_call(F&& f, Args&&... args) {
    ScopedTimer t;
    return std::forward<F>(f)(std::forward<Args>(args)...);   // returns T& if f returns T&, T if it returns T
}
```

With plain `auto` the wrapper would strip references and silently copy. With `decltype(auto)` it's transparent.

The danger is that parentheses change the meaning:

```cpp
decltype(auto) bad() {
    std::vector<int> v = compute();
    return (v);     // decltype((v)) is std::vector<int>&: returns a reference to a local. UB.
}
```

`return v;` would deduce `std::vector<int>` and be fine. Use `decltype(auto)` only in forwarding wrappers, and
never parenthesize the return expression.

### Exercises

:::exercise What does it return?
For each function, give the return type:

```cpp
std::vector<int> g();
int& h();
auto            a() { return h(); }
auto&           b() { return h(); }
decltype(auto)  c() { return h(); }
decltype(auto)  d() { return g(); }
```
:::solution
`a`: `int` (auto strips the reference: a copy). `b`: `int&`. `c`: `int&` (`decltype(h())` is `int&` because the
call is an lvalue). `d`: `std::vector<int>` (`g()` is a prvalue).
:::

---

## 4. `auto` in practice

### Range-for loops

Range-for is where `auto` mistakes are most common, because the loop runs many times:

```cpp
for (auto o : orders)           // copies each Order: slow for anything non-trivial
for (auto& o : orders)          // reference; can modify the elements
for (const auto& o : orders)    // reference; read-only: the default for reading
for (auto&& o : orders)         // forwarding: works even when elements are proxies (vector<bool>, zip views)
```

Use `const auto&` for reading, `auto&` for modifying, `auto` only for small types you want a copy of (like `int`s),
and `auto&&` in generic code where the element type might be a proxy object returned by value.

A classic performance bug with maps:

```cpp
std::map<std::string, Position> positions;
for (const std::pair<std::string, Position>& p : positions) { ... }   // copies every element!
```

The element type is `std::pair<const std::string, Position>`. The `const` on the key means the reference can't
bind directly, so the compiler creates a converted temporary copy for each element. `const auto& [sym, pos]`
avoids the problem entirely.

### When to write the type

`auto` is good when the type is obvious from the right-hand side (`auto it = m.find(k);`,
`auto p = std::make_unique<Book>();`) or unspeakable (lambdas). Write the type explicitly when the exact type
matters for correctness, which is often the case in numeric code:

```cpp
auto qty = 0;                 // int: may overflow when you add large quantities
std::int64_t qty = 0;         // clear and correct

auto ratio = filled / total;  // if both are integers, this is integer division
```

> [!TIP]
> If you can't tell what type `auto` deduced, ask the compiler. Declare `template <class T> struct TD;` (no
> definition) and write `TD<decltype(x)> td;`. The error message names the type exactly.

### Exercises

:::exercise The map copy
Explain why this loop allocates on every iteration, and fix it two ways.

```cpp
std::unordered_map<std::string, double> marks;
for (const std::pair<std::string, double>& kv : marks) total += kv.second;
```
:::solution
The map's `value_type` is `std::pair<const std::string, double>`. A `const std::pair<std::string, double>&`
can't bind to it directly (different type), but there's an implicit conversion, so the compiler creates a
temporary `pair<std::string, double>`, copying the string (which allocates for long strings), and binds the
reference to that. Fix: `for (const auto& kv : marks)`, or `for (const auto& [sym, mark] : marks)`, or spell the
type correctly with `const std::string`.
:::

---

## Summary

- Template deduction has three cases: by value (drops references and top-level `const`, decays arrays), by
  reference (keeps `const`, no decay), and forwarding reference (lvalues deduce `T&`).
- `auto` uses the same rules: plain `auto` always copies; `const auto&` binds without copying; `auto&&` binds to
  anything.
- `decltype(name)` gives the declared type; `decltype((expr))` adds `&` for lvalues.
- `decltype(auto)` preserves references exactly. Use it for forwarding wrappers, and never parenthesize the return.
- In range-for loops, default to `const auto&`. Watch for map element types with a `const` key.
- Write the type explicitly when the exact numeric type matters.

## Key terms

**Template argument deduction** · **decay** · **top-level const** · **forwarding reference** ·
**reference collapsing** · **`auto`** · **return type deduction** · **`decltype`** · **`decltype(auto)`** ·
**proxy object**
