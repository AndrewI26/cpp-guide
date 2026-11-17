+++
title = "Variadic Templates and Fold Expressions"
minutes = 35
practice_tags = ["templates", "metaprogramming"]
+++

## Why this chapter matters

A low-latency logger needs a `log(fmt, args...)` that accepts any number of arguments of any type. It must not
format on the hot path. Instead it copies the raw arguments into a ring buffer for a background thread to format
later (Chapter 12.4). C's `printf`-style varargs can't do this safely: they lose all type information, so the
logger can't know how many bytes to copy or how to decode them later.

**Variadic templates** take any number of template arguments, with full type information for each one. They're
how `std::tuple`, `std::variant`, `std::make_unique`, `emplace_back`, `std::format` and `std::visit` are written.
**Fold expressions** let you apply an operator across all of them in one line. Together they let you write
compile-time loops over types.

By the end of this chapter you should be able to:

- declare and expand parameter packs in function and class templates;
- use fold expressions to sum, combine and call over a pack;
- use `std::tuple`, `std::apply` and `std::index_sequence` to work with heterogeneous collections;
- write a type-safe, allocation-free variadic function like a binary log encoder.

---

## 1. Parameter packs

```cpp
template <class... Ts>
void log(const char* fmt, const Ts&... args);
```

`class... Ts` declares a **template parameter pack**: zero or more types. `const Ts&... args` declares a **function
parameter pack**: one parameter per type. A call `log("px={} qty={}", 100.5, 42)` deduces `Ts = {double, int}`.

The number of elements is `sizeof...(Ts)` (or `sizeof...(args)`), a compile-time constant.

You can't index a pack directly or loop over it at run time. You use it by **expanding** it with `...`. A
pattern followed by `...` is repeated for each element, separated by commas:

```cpp
template <class... Ts>
void forward_all(Ts&&... args) {
    target(std::forward<Ts>(args)...);   // target(std::forward<T1>(a1), std::forward<T2>(a2), ...)
}
```

The pattern can be any expression or type containing the pack:

```cpp
f(args...);                    // f(a1, a2, a3)
f(&args...);                   // f(&a1, &a2, &a3)
f(g(args)...);                 // f(g(a1), g(a2), g(a3))
f(g(args...));                 // f(g(a1, a2, a3))   -- the position of ... matters
std::tuple<Ts*...> ptrs;       // std::tuple<T1*, T2*, T3*>
```

> [!NOTE]
> A pack can be empty. `forward_all()` with no arguments expands `target(...)` to `target()`. Code that uses packs
> should usually work for zero elements; fold expressions over `+` and `*` need an initial value to make that
> possible (section 2).

### Exercises

:::exercise Expand it
With `args` = `(a, b, c)`, what does each expand to? (a) `f(args + 1 ...)`; (b) `f(h(args, args...)...)`;
(c) `std::array arr{static_cast<double>(args)...};`
:::solution
(a) `f(a + 1, b + 1, c + 1)` (the space before `...` stops it parsing as `1...`). (b) The inner `args...` expands
first within each outer element: `f(h(a, a, b, c), h(b, a, b, c), h(c, a, b, c))`. (c) `std::array arr{double(a),
double(b), double(c)};`, which deduces `std::array<double, 3>`.
:::

---

## 2. Fold expressions

C++17 **fold expressions** apply a binary operator across a pack:

```cpp
template <class... Ts>
auto sum(Ts... xs) { return (xs + ...); }            // unary right fold: x1 + (x2 + (x3 + x4))

template <class... Ts>
auto sum0(Ts... xs) { return (0 + ... + xs); }       // binary left fold with initial value: ((0 + x1) + x2) + x3

template <class... Ts>
bool all_positive(Ts... xs) { return ((xs > 0) && ...); }   // true for an empty pack
```

The four forms:

| Form | Expands to |
|---|---|
| `(pack op ...)` | `p1 op (p2 op (... op pN))` |
| `(... op pack)` | `((p1 op p2) op ...) op pN` |
| `(init op ... op pack)` | `((init op p1) op p2) ...` |
| `(pack op ... op init)` | `p1 op (... op (pN op init))` |

A unary fold over an **empty** pack is only allowed for `&&` (gives `true`), `||` (`false`) and `,` (`void()`).
For anything else, use the binary form with an initial value.

### The comma fold: a compile-time loop

Folding over the comma operator calls something once per element, in order:

```cpp
template <class... Ts>
void print_all(const Ts&... args) {
    ((std::cout << args << ' '), ...);
    std::cout << '\n';
}
```

This is the workhorse for "do this for each argument". Because the comma operator evaluates left to right, the
order is guaranteed.

```cpp
template <class... Ts>
constexpr std::size_t total_size() { return (sizeof(Ts) + ... + 0); }

static_assert(total_size<std::int64_t, std::int32_t, char>() == 13);
```

### Exercises

:::exercise Max of a pack
Write `max_of(xs...)` returning the largest argument, assuming all arguments have the same type and there's at
least one.
:::solution
```cpp
template <class T, class... Ts>
T max_of(T first, Ts... rest) {
    T m = first;
    ((m = rest > m ? rest : m), ...);
    return m;
}
```
Taking the first element separately guarantees a non-empty pack and gives a starting value. The comma fold updates
`m` once per remaining argument, left to right. (`std::max({first, rest...})` with an initializer list also works.)
:::

---

## 3. Tuples: packs you can store

A parameter pack only exists inside a template. To store a heterogeneous set of values, use `std::tuple`:

```cpp
std::tuple<std::int64_t, char, double> t{10050, 'B', 0.5};
auto px   = std::get<0>(t);         // by index (compile-time constant)
auto side = std::get<char>(t);      // by type, if unique
auto [p, s, w] = t;                 // structured binding
```

To turn a tuple back into function arguments, use `std::apply`:

```cpp
auto args = std::make_tuple(10050, 100);
std::int64_t n = std::apply([](auto px, auto qty) { return px * qty; }, args);
```

**Layout.** A `std::tuple` stores its elements inline, like a struct, so `sizeof` includes padding. The order of
members in memory is implementation-defined (libstdc++ stores them in *reverse*), so never `memcpy` a tuple to the
wire. For layout-sensitive data, use a plain struct.

### Index sequences

Sometimes you need the *indices* of a pack, for example to iterate over a tuple's elements. `std::index_sequence`
gives you a pack of integers `0, 1, ..., N-1`:

```cpp
template <class Tuple, std::size_t... Is>
void print_tuple_impl(const Tuple& t, std::index_sequence<Is...>) {
    ((std::cout << (Is == 0 ? "" : ", ") << std::get<Is>(t)), ...);
}

template <class... Ts>
void print_tuple(const std::tuple<Ts...>& t) {
    print_tuple_impl(t, std::index_sequence_for<Ts...>{});   // passes index_sequence<0, 1, ..., N-1>
}
```

The helper's parameter `std::index_sequence<Is...>` exists only to let the compiler deduce `Is` as a pack. Since
C++20 you can do this inline with a templated lambda:

```cpp
template <class... Ts>
void print_tuple(const std::tuple<Ts...>& t) {
    [&]<std::size_t... Is>(std::index_sequence<Is...>) {
        ((std::cout << (Is == 0 ? "" : ", ") << std::get<Is>(t)), ...);
    }(std::index_sequence_for<Ts...>{});
}
```

### Exercises

:::exercise Apply to each
Write `for_each_in_tuple(t, f)` that calls `f` on each element of a tuple, in order. Use `std::apply`.
:::solution
```cpp
template <class Tuple, class F>
void for_each_in_tuple(Tuple&& t, F&& f) {
    std::apply([&](auto&&... xs) { (f(std::forward<decltype(xs)>(xs)), ...); }, std::forward<Tuple>(t));
}
```
`std::apply` unpacks the tuple into the lambda's pack, and the comma fold calls `f` on each element. No index
sequences needed.
:::

---

## 4. Variadic class templates

Packs work for classes too. A minimal type-safe event dispatcher keyed on a list of handler types:

```cpp
template <class... Handlers>
class Dispatcher {
public:
    explicit Dispatcher(Handlers&... hs) : handlers_(hs...) {}

    template <class Event>
    void publish(const Event& e) {
        std::apply([&](auto&... h) { (h.get().on(e), ...); }, handlers_);   // every handler, inlined
    }
private:
    std::tuple<std::reference_wrapper<Handlers>...> handlers_;
};

Dispatcher d(risk, strategy, logger);   // CTAD deduces Dispatcher<Risk, Strategy, Logger>
d.publish(quote);                       // three direct, inlinable calls: no virtual dispatch, no std::function
```

Compare with Chapter 2.5's vector of `std::function`: the set of handlers is fixed at compile time, but in
exchange every call is direct. Many trading systems are configured exactly like this, with the full pipeline's
types known when the binary is built.

---

## 5. Worked example: a binary log encoder

Here is the core of the deferred-formatting logger from the introduction. At the call site, we compute the exact
encoded size of the arguments at compile time, reserve that many bytes in a buffer, and `memcpy` each argument in.
The format string pointer is stored too; formatting happens later, on another thread.

```cpp
template <class T>
concept Loggable = std::is_trivially_copyable_v<T> && !std::is_pointer_v<T>;   // no dangling pointers

template <Loggable... Ts>
void log_binary(std::byte* out, const char* fmt, const Ts&... args) {
    std::memcpy(out, &fmt, sizeof fmt);
    std::size_t off = sizeof fmt;
    ((std::memcpy(out + off, &args, sizeof(Ts)), off += sizeof(Ts)), ...);
}

template <class... Ts>
constexpr std::size_t encoded_size = sizeof(const char*) + (sizeof(Ts) + ... + 0);

static_assert(encoded_size<std::int64_t, double, char> == 8 + 8 + 8 + 1);
```

The decoder on the logging thread needs to know the argument types to read them back. A full implementation
stores a pointer to a decoder function instantiated for exactly `Ts...`, which is just another template. The
practice project builds the whole thing.

> [!PERF]
> Everything about the argument list (count, sizes, offsets, decode function) is resolved at compile time. The
> hot-path cost of `log_binary` is a few `mov` instructions per argument. Formatting the same call with
> `snprintf` costs hundreds of nanoseconds.

---

## Summary

- `class... Ts` and `Ts... args` declare parameter packs; `sizeof...` counts them; `pattern...` expands them.
- The position of `...` determines what's repeated.
- Fold expressions apply an operator across a pack. Unary folds over empty packs work only for `&&`, `||` and `,`;
  otherwise give an initial value.
- The comma fold `(f(args), ...)` is the standard compile-time "for each".
- `std::tuple` stores a pack; `std::apply` and `std::index_sequence` turn it back into one.
- Variadic class templates let you build fully static pipelines with direct, inlinable calls.

## Key terms

**Parameter pack** · **pack expansion** · **`sizeof...`** · **fold expression** · **comma fold** ·
**`std::tuple`** · **`std::apply`** · **`std::index_sequence`** · **templated lambda**
