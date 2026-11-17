+++
title = "Function and Class Templates"
minutes = 40
practice_tags = ["templates", "data-structures"]
+++

## Why this chapter matters

You need a fixed-capacity ring buffer for prices. Then one for `Quote`s. Then one for `Order*`s. Copying the class
three times means three places to fix every bug. Storing `void*` and casting means no type checking and an extra
indirection for every element. Using a base class with virtual functions means heap-allocated elements and
indirect calls.

**Templates** give you a fourth option: write the code once, with the element type as a parameter, and let the
compiler generate a specialized version for each type you use. Each generated version is as fast as if you'd
written it by hand for that type. This is how the entire standard library works, and it's the foundation of
zero-cost abstraction in C++.

By the end of this chapter you should be able to:

- write function and class templates with type and non-type parameters;
- explain instantiation: what code is generated, when, and where;
- write full and partial specializations;
- organize template code in headers, and understand the compile-time and code-size costs.

---

## 1. Function templates

```cpp
template <class T>
T clamp_to(T value, T lo, T hi) {
    return value < lo ? lo : (hi < value ? hi : value);
}

std::int64_t q = clamp_to<std::int64_t>(qty, 0, max_qty);   // explicit template argument
double px      = clamp_to(price, 0.0, 1e6);                 // T deduced as double
```

`template <class T>` (or equivalently `template <typename T>`) introduces a **template parameter**. The function
isn't a function yet: it's a pattern. When you call it, the compiler **deduces** `T` from the arguments and
**instantiates** a real function with `T` replaced.

Deduction requires the argument types to agree. `clamp_to(qty, 0, max_qty)` where `qty` is `int64_t` and `0` is
`int` fails: `T` can't be both. Fix it with an explicit argument, as above, or by giving the parameters separate
template types. Chapter 3.2 covers the deduction rules in detail.

### What the compiler generates

For each distinct set of template arguments used, the compiler generates one function. After instantiation, the
two calls above behave as if you had written:

```cpp
std::int64_t clamp_to(std::int64_t value, std::int64_t lo, std::int64_t hi) { /* ... */ }
double       clamp_to(double value, double lo, double hi)                   { /* ... */ }
```

Each is compiled and optimized separately, with full knowledge of the concrete type. Comparisons on `int64_t`
become integer compares; on `double`, floating-point ones. There's no run-time dispatch, no boxing, no indirection.
That's the essential difference from generics in Java or C#.

---

## 2. Class templates

```cpp
template <class T, std::size_t Capacity>
class RingBuffer {
    static_assert(Capacity > 0 && (Capacity & (Capacity - 1)) == 0, "Capacity must be a power of two");
public:
    bool push(const T& value) {
        if (size() == Capacity) return false;
        data_[head_++ & (Capacity - 1)] = value;
        return true;
    }
    bool pop(T& out) {
        if (size() == 0) return false;
        out = data_[tail_++ & (Capacity - 1)];
        return true;
    }
    std::size_t size() const { return head_ - tail_; }
    static constexpr std::size_t capacity() { return Capacity; }
private:
    std::array<T, Capacity> data_{};
    std::size_t head_ = 0, tail_ = 0;
};

RingBuffer<double, 1024> prices;
RingBuffer<Quote, 256>   quotes;
```

`Capacity` is a **non-type template parameter**: a compile-time constant rather than a type. Making the capacity a
template parameter has real performance benefits. The storage is inline (no heap), and `& (Capacity - 1)` is a
constant mask that compiles to a single `and` instruction. A run-time capacity would need a load and possibly a
division.

Non-type parameters can be integers, enums, pointers, and since C++20, floating-point values and simple
("structural") class types.

Since C++17, class template arguments can often be deduced from constructor arguments (**CTAD**):

```cpp
std::vector v{1.0, 2.0, 3.0};     // std::vector<double>
std::pair p{10050LL, 'B'};        // std::pair<long long, char>
std::array a{1, 2, 3};            // std::array<int, 3>
```

### Member functions are instantiated lazily

A member function of a class template is only instantiated if it's actually used. That's why you can have a
`std::map<OrderId, V>` where `V` has no default constructor, as long as you never call `operator[]` (which
default-constructs missing entries): the code requiring `V()` is never generated. Use `find` and `try_emplace`
instead and everything compiles.

### Exercises

:::exercise Two instantiations or one?
How many distinct class types are created by `RingBuffer<int, 64> a; RingBuffer<int, 64> b; RingBuffer<int, 128> c;
RingBuffer<long, 64> d;`? Can you assign `a = c`?
:::solution
Three: `RingBuffer<int, 64>` (shared by `a` and `b`), `RingBuffer<int, 128>` and `RingBuffer<long, 64>`. `a = c`
doesn't compile: different template arguments produce completely unrelated types, with no conversion between
them.
:::

---

## 3. Instantiation, headers, and the cost of templates

To instantiate a template, the compiler needs its full **definition**, not just a declaration. So template code
almost always lives entirely in headers. (A template's definitions are exempt from the One Definition Rule's
"exactly one definition" requirement, the same way `inline` functions are; Chapter 1.1.)

This has costs:

- **Compile time.** Every translation unit that uses `RingBuffer<double, 1024>` instantiates and compiles it.
  The linker then throws away the duplicates. Heavy template code (Eigen, Boost.Spirit, deep `std::ranges`
  pipelines) can make individual files take tens of seconds to compile.
- **Code size ("bloat").** Each distinct instantiation is separate machine code. `RingBuffer<T, N>` for ten types
  and five capacities is fifty copies of the code. Usually that's fine. It becomes a problem when it pushes hot
  code out of the instruction cache.
- **Error messages.** An error deep inside an instantiation is reported with the full chain of instantiations that
  led there. Concepts (Chapter 3.4) make this much better.

### Explicit instantiation

When a template is only used with a few known arguments, you can move its definition into a `.cpp` file and
instantiate it there explicitly:

```cpp
// pricer.hpp
template <class Real> Real bs_price(Real s, Real k, Real r, Real vol, Real t);
extern template double bs_price<double>(double, double, double, double, double);   // "don't instantiate here"

// pricer.cpp
template <class Real> Real bs_price(Real s, Real k, Real r, Real vol, Real t) { /* ... */ }
template double bs_price<double>(double, double, double, double, double);          // instantiate once, here
template float  bs_price<float>(float, float, float, float, float);
```

This cuts compile times, but the body is no longer visible to callers, so it can't be inlined into them (without
LTO). Use it for big functions that aren't inlining candidates anyway.

> [!PERF]
> Templates trade compile time and code size for run-time speed: each instantiation is specialized and fully
> optimizable. If you see a template function taking 5% of i-cache misses in a profile, consider factoring the
> type-independent part into a non-template helper that all instantiations share.

---

## 4. Specialization

Sometimes a particular type argument needs a different implementation. A **full (explicit) specialization**
provides one:

```cpp
template <class T>
struct Hash {
    std::size_t operator()(const T& v) const { return std::hash<T>{}(v); }
};

template <>
struct Hash<std::uint64_t> {   // order IDs: use a better mixer than std::hash's identity function
    std::size_t operator()(std::uint64_t x) const {
        x ^= x >> 33; x *= 0xff51afd7ed558ccdULL; x ^= x >> 33;
        return x;
    }
};
```

A class template can also be **partially specialized**: specialized for a family of arguments.

```cpp
template <class T> struct IsPointer      : std::false_type {};
template <class T> struct IsPointer<T*>  : std::true_type {};   // matches any pointer type

static_assert(IsPointer<int*>::value && !IsPointer<int>::value);
```

Function templates can only be **fully** specialized, and even that is discouraged: specializations don't take part
in overload resolution, which leads to surprising results. To customize a function template for a type, write an
ordinary **overload** instead.

```cpp
template <class T> void print(const T& v);   // general
void print(const Price& p);                  // overload for Price: preferred over the template when it matches
```

> [!NOTE]
> The standard library's famous specialization mistake is `std::vector<bool>`, which packs bits instead of
> storing `bool`s. Its `operator[]` returns a proxy object rather than a `bool&`, so `auto& b = v[0];` doesn't
> compile and generic code that works for every other `vector<T>` breaks. When you need a vector of flags that
> behaves normally, use `std::vector<std::uint8_t>`, or `std::vector<char>`.

### Exercises

:::exercise Partial specialization
Write a class template `ElementCount<T>` with a `static constexpr std::size_t value` that is 1 for most types and
`N` for `std::array<U, N>`.
:::solution
```cpp
template <class T>
struct ElementCount { static constexpr std::size_t value = 1; };

template <class U, std::size_t N>
struct ElementCount<std::array<U, N>> { static constexpr std::size_t value = N; };

static_assert(ElementCount<double>::value == 1);
static_assert(ElementCount<std::array<double, 8>>::value == 8);
```
The partial specialization matches any `std::array`, deducing `U` and `N` from the argument.
:::

---

## 5. Dependent names: `typename` and `template`

Inside a template, a name that depends on a template parameter is a **dependent name**, and the compiler doesn't
know what it refers to until instantiation. By default it assumes a dependent name is a *value*. When it's a type,
you must say so with `typename`:

```cpp
template <class Container>
void print_first(const Container& c) {
    typename Container::value_type first = *c.begin();   // without 'typename': parse error
    // ...
}
```

Similarly, calling a member *template* through a dependent name needs `template`:

```cpp
template <class Tuple>
auto first_of(const Tuple& t) { return t.template get<0>(); }   // without 'template', '<' parses as less-than
```

C++20 relaxed the `typename` requirement in many contexts where only a type could appear (return types, aliases,
parameter declarations). In practice: if the compiler complains about a "dependent name", add `typename`; for
`value_type`-style aliases, `using T = typename Container::value_type;` once near the top keeps the rest readable.

> [!TIP]
> Modern code often sidesteps the problem entirely with `auto`, or with standard aliases like
> `std::ranges::range_value_t<R>`.

---

## Summary

- Templates are patterns. Each distinct set of template arguments instantiates separate, fully specialized code,
  with no run-time overhead.
- Function template arguments are usually deduced; class template arguments can be deduced from constructors
  (CTAD).
- Non-type template parameters make sizes and constants compile-time values, enabling inline storage and constant
  folding.
- Template definitions belong in headers. The costs are compile time and code size; explicit instantiation trades
  inlining for faster builds.
- Specialize class templates fully or partially; for function templates, overload instead.
- Mark dependent type names with `typename`.

## Key terms

**Template** · **template parameter** · **instantiation** · **template argument deduction** ·
**non-type template parameter** · **CTAD** · **explicit instantiation** · **code bloat** · **full specialization** ·
**partial specialization** · **dependent name**
