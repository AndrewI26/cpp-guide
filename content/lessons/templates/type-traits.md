+++
title = "Type Traits and Metaprogramming"
minutes = 40
practice_tags = ["metaprogramming", "templates"]
+++

## Why this chapter matters

A generic ring buffer copies elements in and out. For a `Quote` struct of integers, the fastest way is `memcpy`, or
a plain assignment the compiler turns into a couple of vector moves. For a `std::string`, `memcpy` would corrupt the
heap: the string owns a pointer to its buffer, and two copies of that pointer means two owners. The ring buffer
needs to know, at compile time, which kind of type it's holding.

**Type traits** answer questions about types at compile time: is this type trivially copyable? Is it a pointer? What
is `T` without its reference? The standard library's `<type_traits>` provides about a hundred of them. With
`if constexpr`, `static_assert` and concepts, they let generic code choose the best implementation for each type,
and refuse types it can't handle.

By the end of this chapter you should be able to:

- use the most important standard traits, and their `_v` and `_t` shortcuts;
- explain how a trait is implemented with specialization, and write your own;
- transform types with `remove_cvref`, `decay`, `conditional` and friends;
- recognize SFINAE and `enable_if` in existing code, and know when you'd still need them.

---

## 1. Standard traits

A **type trait** is a class template whose instantiation carries a compile-time answer. There are two kinds.

**Predicates** answer yes/no questions through a `static constexpr bool value`:

```cpp
std::is_integral<int>::value             // true
std::is_integral_v<double>               // false  (the _v variable template is the short form)
std::is_trivially_copyable_v<Quote>      // true for a struct of integers
std::is_trivially_copyable_v<std::string>// false
std::is_same_v<std::int64_t, long>       // true on Linux x86-64
```

**Transformations** compute a new type through a nested `type`:

```cpp
std::remove_reference<const int&>::type  // const int
std::remove_cvref_t<const int&>          // int        (the _t alias template is the short form)
std::make_unsigned_t<std::int64_t>       // std::uint64_t
std::decay_t<const char[5]>              // const char*
std::conditional_t<(sizeof(void*) == 8), std::uint64_t, std::uint32_t>   // pick a type with a condition
```

The traits that matter most in performance-oriented code:

| Trait | Question | Why it matters |
|---|---|---|
| `is_trivially_copyable_v<T>` | can it be copied with `memcpy`? | ring buffers, shared memory, wire codecs, `std::bit_cast` |
| `is_trivially_destructible_v<T>` | does destruction do nothing? | pools and arenas can skip destructor calls |
| `is_nothrow_move_constructible_v<T>` | can moving throw? | containers move instead of copy (Chapter 2.3) |
| `is_standard_layout_v<T>` | is its layout C-compatible and predictable? | `offsetof`, interop, wire structs |
| `is_aggregate_v<T>` | is it a plain struct with no constructors? | designated initializers, structured bindings |
| `is_integral_v`, `is_floating_point_v`, `is_signed_v` | numeric category | numeric code paths |
| `has_unique_object_representations_v<T>` | is every bit significant (no padding)? | hashing or comparing objects bytewise |

### Using them

Combine traits with `static_assert`, `if constexpr` and concepts:

```cpp
template <class T, std::size_t N>
class RingBuffer {
    static_assert(std::is_trivially_copyable_v<T>, "RingBuffer<T> copies with memcpy");
    // ...
};

template <class T>
void copy_n(const T* src, std::size_t n, T* dst) {
    if constexpr (std::is_trivially_copyable_v<T>) {
        std::memcpy(dst, src, n * sizeof(T));          // one call, vectorized by libc
    } else {
        for (std::size_t i = 0; i < n; ++i) dst[i] = src[i];
    }
}
```

(Standard algorithms like `std::copy` already do this dispatch internally, which is one reason to prefer them.)

### Exercises

:::exercise Trivially copyable?
Which of these are trivially copyable? (a) `struct A { int x; double y; };` (b) `struct B { int x; B(const B&) {} };`
(c) `struct C { std::array<char, 8> sym; std::int64_t px; };` (d) `struct D { std::string_view s; };`
(e) `struct E { virtual void f(); };`
:::solution
(a) Yes. (b) No: a user-provided copy constructor, even an empty one, isn't trivial. (c) Yes: `std::array` of a
trivially copyable type is trivially copyable. (d) Yes: `string_view` is a pointer and a length, trivially
copyable. Copying it with `memcpy` is fine, but the copy points at the same characters, so lifetime is still your
problem. (e) No: classes with virtual functions aren't trivially copyable (the vptr must be set by the
constructor).
:::

---

## 2. How traits are implemented

Most traits are just templates with specializations. A boolean trait starts from `std::false_type` and specializes
the cases that are true:

```cpp
template <class T> struct is_pointer_ : std::false_type {};
template <class T> struct is_pointer_<T*> : std::true_type {};
template <class T> struct is_pointer_<T* const> : std::true_type {};
template <class T> inline constexpr bool is_pointer_v_ = is_pointer_<T>::value;
```

`std::true_type` and `std::false_type` are `std::integral_constant<bool, true/false>`: a struct with a `static
constexpr bool value`. Inheriting from them gives you `value` for free.

A transformation trait specializes to strip or add parts of a type:

```cpp
template <class T> struct remove_ref_       { using type = T; };
template <class T> struct remove_ref_<T&>   { using type = T; };
template <class T> struct remove_ref_<T&&>  { using type = T; };
template <class T> using remove_ref_t_ = typename remove_ref_<T>::type;
```

Some traits can't be written in plain C++ and are implemented with **compiler intrinsics**: `is_trivially_copyable`
uses `__is_trivially_copyable(T)`, `is_standard_layout` uses `__is_standard_layout(T)`. Only the compiler knows
those properties.

### Your own traits

Domain-specific traits are useful for marking types:

```cpp
template <class T> struct is_price_type : std::false_type {};
template <>        struct is_price_type<Ticks> : std::true_type {};
template <>        struct is_price_type<FixedPoint<4>> : std::true_type {};

template <class T>
concept PriceType = is_price_type<T>::value;
```

Now a library function can accept only price types, and a user who adds a new price type opts in by specializing
`is_price_type`. This "trait as customization point" pattern is how libraries like `fmt` and `nlohmann::json`
learn about your types.

### Exercises

:::exercise Write `is_std_vector`
Write a trait `is_std_vector<T>` that's true for any `std::vector<U, A>` and false otherwise, with an `_v`
variable template.
:::solution
```cpp
template <class T> struct is_std_vector : std::false_type {};
template <class U, class A> struct is_std_vector<std::vector<U, A>> : std::true_type {};
template <class T> inline constexpr bool is_std_vector_v = is_std_vector<std::remove_cvref_t<T>>::value;

static_assert(is_std_vector_v<std::vector<int>>);
static_assert(is_std_vector_v<const std::vector<double>&>);
static_assert(!is_std_vector_v<std::array<int, 3>>);
```
The `remove_cvref_t` in the `_v` alias makes it work for `const` and reference types, which is usually what you
want when testing a deduced parameter type.
:::

---

## 3. Choosing types at compile time

Traits that compute types let you adapt data structures to their parameters:

```cpp
template <std::size_t MaxValue>
using smallest_uint_t =
    std::conditional_t<(MaxValue <= 0xFF),       std::uint8_t,
    std::conditional_t<(MaxValue <= 0xFFFF),     std::uint16_t,
    std::conditional_t<(MaxValue <= 0xFFFFFFFF), std::uint32_t,
                                                 std::uint64_t>>>;

template <std::size_t Capacity>
struct IndexList {
    using index_t = smallest_uint_t<Capacity>;   // a pool of 50,000 orders uses 16-bit links
    std::array<index_t, Capacity> next;
};

static_assert(sizeof(IndexList<50'000>::index_t) == 2);
```

Smaller indices mean smaller nodes, which means more of them per cache line (Chapter 5.3). This is the kind of
thing metaprogramming is genuinely for in performance work: computing layouts and sizes, not clever tricks.

---

## 4. SFINAE and `enable_if`: reading older code

Before concepts, overloading templates on type properties relied on **SFINAE**: "Substitution Failure Is Not An
Error." When the compiler substitutes template arguments into a function template's *signature* and the result is
ill-formed, that template is silently removed from the overload set instead of causing an error.

`std::enable_if_t<Cond, T>` exploits this: it's `T` when `Cond` is true, and ill-formed (no nested `type`) when it's
false.

```cpp
// Pre-C++20: only participates in overload resolution for integral T
template <class T, std::enable_if_t<std::is_integral_v<T>, int> = 0>
T round_to_lot(T qty, T lot) { return qty / lot * lot; }

// C++20
template <std::integral T>
T round_to_lot(T qty, T lot) { return qty / lot * lot; }
```

Another classic SFINAE idiom detects whether an expression is valid using `std::void_t`:

```cpp
template <class T, class = void>
struct has_reserve : std::false_type {};

template <class T>
struct has_reserve<T, std::void_t<decltype(std::declval<T&>().reserve(std::size_t{}))>> : std::true_type {};

// C++20 equivalent
template <class T>
concept HasReserve = requires(T& t) { t.reserve(std::size_t{}); };
```

`std::declval<T>()` produces a value of type `T` in an unevaluated context (like inside `decltype`), even for types
you can't construct.

You'll meet these patterns in every pre-2020 codebase and in libraries that still support C++17. In new C++20 code,
use concepts, `requires` expressions and `if constexpr` instead. They do the same thing and are far easier to read.

> [!NOTE]
> SFINAE only applies to the *immediate context* of the signature. An error inside a function body after
> overload resolution has chosen it is a hard error, not a substitution failure. That's why traits test
> expressions inside `decltype` in the signature or a partial specialization.

### Exercises

:::exercise Translate to C++20
Rewrite with a concept or `requires` clause:

```cpp
template <class T, class = std::enable_if_t<std::is_trivially_copyable_v<T> && sizeof(T) <= 64>>
void publish(const T& msg);
```
:::solution
```cpp
template <class T>
concept SmallPod = std::is_trivially_copyable_v<T> && (sizeof(T) <= 64);

template <SmallPod T>
void publish(const T& msg);
```
or inline: `template <class T> requires std::is_trivially_copyable_v<T> && (sizeof(T) <= 64) void publish(const T&);`.
The parentheses around `sizeof(T) <= 64` are required in a `requires` clause, since only primary expressions
can appear unparenthesized there.
:::

---

## Summary

- Type traits answer questions about types (`_v` predicates) and compute new types (`_t` transformations) at
  compile time.
- Most valuable for performance: `is_trivially_copyable`, `is_trivially_destructible`,
  `is_nothrow_move_constructible`, `is_standard_layout`.
- Traits are implemented with partial specialization, or compiler intrinsics where the language can't express the
  property.
- Your own traits make good opt-in customization points.
- `conditional_t` and friends compute types, for example the smallest integer that can index a pool.
- SFINAE, `enable_if` and `void_t` detection are the pre-C++20 way of doing what concepts now do. Recognize them;
  write concepts.

## Key terms

**Type trait** · **predicate trait** · **transformation trait** · **`integral_constant`** · **compiler intrinsic** ·
**trivially copyable** · **customization point** · **`conditional_t`** · **SFINAE** · **`enable_if`** ·
**`void_t`** · **`declval`**
