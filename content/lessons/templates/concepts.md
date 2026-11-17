+++
title = "Concepts and Constraints"
minutes = 35
practice_tags = ["templates", "metaprogramming"]
+++

## Why this chapter matters

You call a generic VWAP function with the wrong kind of container:

```cpp
template <class Trades>
double vwap(const Trades& trades) {
    double notional = 0, volume = 0;
    for (const auto& t : trades) { notional += t.price * t.qty; volume += t.qty; }
    return notional / volume;
}

std::map<std::uint64_t, Trade> by_id = ...;
vwap(by_id);
```

The compiler responds with forty lines about `std::pair<const unsigned long, Trade>` having no member named
`price`, pointing into the body of `vwap`, three levels down an instantiation stack. The actual problem, "you
passed a map of pairs where a range of trades was expected", is never stated.

**Concepts** (C++20) let a template say what it requires of its arguments, up front, in its signature. Errors are
reported at the call site in terms of the requirement that failed. And constraints let you overload templates on
*what types can do*, which replaces most of the arcane `enable_if` tricks of older C++.

By the end of this chapter you should be able to:

- constrain templates with standard concepts using `requires` clauses, constrained parameters and `auto`;
- write your own concepts with `requires` expressions;
- overload function templates on constraints, and know how the most constrained one is chosen;
- design concepts that describe meaningful capabilities rather than syntax.

---

## 1. Using standard concepts

The standard library defines many concepts in `<concepts>`, `<iterator>` and `<ranges>`. A concept is a named,
compile-time predicate on types:

```cpp
static_assert(std::integral<std::int64_t>);
static_assert(!std::integral<double>);
static_assert(std::floating_point<double>);
static_assert(std::ranges::random_access_range<std::vector<Trade>>);
```

There are three equivalent ways to apply one to a template:

```cpp
// 1. requires clause
template <class T>
    requires std::integral<T>
T round_to_lot(T qty, T lot) { return qty / lot * lot; }

// 2. constrained template parameter
template <std::integral T>
T round_to_lot(T qty, T lot);

// 3. constrained auto (abbreviated function template)
std::integral auto round_to_lot(std::integral auto qty, std::integral auto lot);
```

Form 3 gives each parameter its own independent type, so it's not quite the same as forms 1 and 2. Use whichever
reads best; form 2 is the most common for single-parameter constraints.

Now a wrong call fails at the call site with a clear message:

```text
error: no matching function for call to 'round_to_lot(double, double)'
note: candidate: 'template<class T>  requires  integral<T> T round_to_lot(T, T)'
note: constraints not satisfied
note: the expression 'is_integral_v<_Tp> [with _Tp = double]' evaluated to 'false'
```

### Commonly used standard concepts

| Concept | Satisfied by |
|---|---|
| `std::integral<T>`, `std::floating_point<T>` | built-in integer / floating types |
| `std::same_as<T, U>` | exactly the same type |
| `std::convertible_to<From, To>` | implicitly convertible |
| `std::totally_ordered<T>` | `<`, `>`, `<=`, `>=`, `==` all work consistently |
| `std::copyable<T>`, `std::movable<T>`, `std::regular<T>` | value-like types |
| `std::invocable<F, Args...>` | `F` can be called with `Args...` |
| `std::predicate<F, Args...>` | invocable and returns something convertible to `bool` |
| `std::ranges::range<R>`, `forward_range`, `random_access_range`, `contiguous_range` | ranges by iterator category |
| `std::ranges::sized_range<R>` | `std::ranges::size(r)` works in O(1) |

### Exercises

:::exercise Constrain VWAP
Constrain the `vwap` template from the introduction so that it accepts only input ranges. Does that make the
`std::map` error message better? What would make it better still?
:::solution
```cpp
template <std::ranges::input_range Trades>
double vwap(const Trades& trades);
```
This doesn't help for `std::map`: a map *is* an input range, so the constraint passes and the error still comes
from inside the body. To catch it at the call site, also constrain the element type, e.g. with a concept that
requires `.price` and `.qty` members (section 2):
`template <std::ranges::input_range R> requires TradeLike<std::ranges::range_value_t<R>>`.
:::

---

## 2. Writing concepts

A concept is defined with a constraint expression, which is usually a combination of other concepts, type traits
and **requires expressions**:

```cpp
template <class T>
concept TradeLike = requires(const T& t) {
    { t.price } -> std::convertible_to<double>;
    { t.qty }   -> std::convertible_to<double>;
};
```

A `requires` expression lists requirements that must all compile. There are four kinds:

```cpp
template <class B>
concept OrderBookLike = requires(B book, const B cbook, std::uint64_t id, std::int64_t px, std::int64_t qty) {
    typename B::level_type;                                   // type requirement: B has a nested type
    book.add(id, px, qty);                                    // simple requirement: expression compiles
    { cbook.best_bid() } -> std::same_as<std::int64_t>;       // compound: compiles, and the result type satisfies a concept
    { book.cancel(id) } noexcept;                             // compound: compiles and is noexcept
    requires sizeof(typename B::level_type) <= 64;            // nested requirement: a constant must be true
};
```

The requirements are only checked for **validity**; nothing is evaluated. `book.add(id, px, qty)` just has to be a
well-formed expression.

Concepts compose with `&&` and `||`:

```cpp
template <class T>
concept Price = std::integral<T> && (sizeof(T) == 8);
```

### Design concepts around meaning

It's tempting to write a concept for every individual operation (`HasPlus`, `HasLess`...). The standard's advice,
and good practice, is the opposite: a concept should describe a meaningful abstraction with **semantic**
requirements, even though the compiler can only check the syntax. `std::totally_ordered` doesn't just mean "has
`<`"; it means `<` is a strict total order consistent with `==`. Your `OrderBookLike` should mean "behaves like an
order book", documented in a comment, not "has these four member functions".

> [!TIP]
> A concept's syntactic requirements are a minimum. Document the semantic ones in a comment above it, as the
> standard does: "`best_bid()` returns the highest resting buy price, or 0 if there are no bids; `cancel` on an
> unknown id is a no-op."

### Exercises

:::exercise Write a concept
Write a concept `MarketDataHandler<H>` requiring that `h.on_quote(q)` and `h.on_trade(t)` compile for a non-const
`H& h`, `const Quote& q` and `const Trade& t`, and that `on_quote` is `noexcept`.
:::solution
```cpp
template <class H>
concept MarketDataHandler = requires(H& h, const Quote& q, const Trade& t) {
    { h.on_quote(q) } noexcept;
    h.on_trade(t);
};
```
Then `template <MarketDataHandler H> class FeedHandler { ... };` rejects unsuitable handler types at the point of
use, with an error naming the missing function.
:::

---

## 3. Overloading on constraints

Constraints take part in overload resolution. When several templates match, the compiler picks the **most
constrained** one: the one whose constraints imply the others'.

```cpp
template <std::ranges::forward_range R>
decltype(auto) element_at(R&& r, std::size_t n) {   // general case: walk n steps, O(n)
    auto it = std::ranges::begin(r);
    for (std::size_t i = 0; i < n; ++i) ++it;
    return *it;
}

template <std::ranges::random_access_range R>
decltype(auto) element_at(R&& r, std::size_t n) {   // random access: jump straight there, O(1)
    return std::ranges::begin(r)[n];
}
```

`random_access_range` is defined in terms of `bidirectional_range`, which is defined in terms of `forward_range`,
so the compiler knows it's strictly more constrained. For a `std::vector`, both match, and the O(1) version wins.
For a `std::list`, only the first matches.

This is called **subsumption**, and it only works when constraints are built from the same named concepts. Two
concepts with identical expressions written separately aren't known to be related:

```cpp
template <class T> concept A = std::is_integral_v<T>;
template <class T> concept B = std::is_integral_v<T> && sizeof(T) == 8;   // doesn't subsume A!
template <class T> concept C = A<T> && sizeof(T) == 8;                    // subsumes A
```

`B` repeats the expression instead of naming `A`, so the compiler treats them as unrelated and an overload pair
on `A` and `B` is ambiguous for `int64_t`. Build concepts from other concepts.

### Replacing tag dispatch and `enable_if`

Before C++20, overloading on type properties required SFINAE tricks (Chapter 3.7):

```cpp
template <class T, std::enable_if_t<std::is_integral_v<T>, int> = 0>
void store(T v);

template <class T, std::enable_if_t<std::is_floating_point_v<T>, int> = 0>
void store(T v);
```

With concepts:

```cpp
void store(std::integral auto v);
void store(std::floating_point auto v);
```

Same behaviour, readable, with good error messages. For new code, concepts or `if constexpr` should cover nearly
every case.

> [!NOTE]
> Concepts are purely compile-time. They generate no code and have zero run-time cost. They change which function
> is chosen, and how errors are reported, nothing else.

### Exercises

:::exercise Which overload?
Given `void f(std::integral auto); void f(std::signed_integral auto); void f(auto);`, which is called for
`f(1)`, `f(1u)` and `f(1.0)`?
:::solution
`f(1)`: all three match; `std::signed_integral` is defined as `std::integral<T> && std::is_signed_v<T>`, so it
subsumes `std::integral`, and both subsume the unconstrained one. The `signed_integral` overload is chosen.
`f(1u)`: `integral` and unconstrained match; `integral` is more constrained. `f(1.0)`: only the unconstrained
overload matches.
:::

---

## 4. Constraining class templates and members

Concepts work on class templates too, and on individual member functions:

```cpp
template <class T, std::size_t N>
    requires std::is_trivially_copyable_v<T>      // the ring buffer copies with memcpy
class SpscRing {
public:
    bool push(const T& v);

    T sum() const requires std::is_arithmetic_v<T> {   // only exists for arithmetic T
        T s{};
        for (std::size_t i = 0; i < size(); ++i) s += at(i);
        return s;
    }
    // ...
};
```

A constrained member function only exists when its constraint holds. `SpscRing<Quote, 1024>` has no `sum()`, and
calling it gives a clear "constraints not satisfied" error rather than an error inside the loop.

---

## Summary

- A concept is a named compile-time predicate on types. Apply it with a `requires` clause, a constrained template
  parameter, or constrained `auto`.
- Errors from constrained templates appear at the call site and name the failed requirement.
- Write concepts with `requires` expressions: simple, type, compound (`{ expr } -> concept`, `noexcept`), and
  nested requirements.
- Design concepts around meaningful abstractions and document their semantic requirements.
- Overloads are chosen by subsumption: the most constrained match wins, but only when concepts are built from
  each other by name.
- Concepts replace most uses of `enable_if` and tag dispatch, with zero run-time cost.

## Key terms

**Concept** · **constraint** · **`requires` clause** · **`requires` expression** · **constrained parameter** ·
**compound requirement** · **subsumption** · **most constrained** · **semantic requirement**
