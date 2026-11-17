+++
title = "constexpr, consteval and Compile-Time Computation"
minutes = 40
practice_tags = ["constexpr", "templates", "numerics"]
+++

## Why this chapter matters

A price-conversion routine turns a decimal price string into integer ticks. Each call computes `10^k` for some
number of decimal places `k` with a loop, or worse, with `std::pow`, a floating-point library call that takes tens
of nanoseconds and can be off by one ulp. Replacing it with a table of the nineteen powers of ten that fit in 64
bits, computed **by the compiler** and baked into the binary, turns that work into a single load.

Anything that can be known when the program is built doesn't have to be computed while it runs. C++ lets you run
ordinary-looking functions at compile time with `constexpr` and `consteval`, so lookup tables, configuration
validation, protocol field offsets and bit masks can all be computed and checked by the compiler. And because a
compile-time computation that hits undefined behaviour is a compile error, it's also a way of proving code correct.

By the end of this chapter you should be able to:

- write `constexpr` functions and variables, and know what they may and may not do;
- explain when a `constexpr` function actually runs at compile time;
- use `consteval` and `constinit` and say when each is the right tool;
- build lookup tables and validate data at compile time;
- use `if constexpr` and `static_assert` to make compile-time decisions.

---

## 1. Constant expressions

A **constant expression** is one the compiler can evaluate while compiling. Literals, arithmetic on them, and
enumerators are constant expressions. So are calls to `constexpr` functions with constant arguments.

They're required wherever the language needs a value at compile time: array bounds, template arguments,
`static_assert`, `case` labels, and the initializer of a `constexpr` variable.

```cpp
constexpr std::int64_t kTicksPerDollar = 100;
constexpr std::int64_t kMaxPriceTicks  = 1'000'000 * kTicksPerDollar;   // computed by the compiler
std::array<std::int32_t, kMaxPriceTicks / 1000> buckets{};              // usable as a template argument
static_assert(kMaxPriceTicks < (std::int64_t{1} << 40));               // checked at compile time
```

A `constexpr` **variable** must be initialized with a constant expression, and is implicitly `const`. Prefer it to
`const` for compile-time constants: it guarantees the value is computed at compile time, and fails to compile
otherwise. And prefer it to `#define` (Chapter 1.1): it has a type and a scope.

---

## 2. `constexpr` functions

A function marked `constexpr` **may** be evaluated at compile time if its arguments are constant expressions:

```cpp
constexpr std::int64_t pow10(int n) {
    std::int64_t r = 1;
    for (int i = 0; i < n; ++i) r *= 10;
    return r;
}

constexpr std::int64_t a = pow10(6);   // compile time: a is the literal 1000000 in the binary
std::int64_t b = pow10(user_input);    // run time: an ordinary function call
```

The same function works in both worlds, so you write it once. Since C++14, `constexpr` functions can contain
loops, local variables and branches; since C++20, they can also use `try` blocks, virtual calls, `std::vector`
and `std::string` (as long as any memory allocated is freed before the evaluation ends), and change the active
member of a union.

What they still can't do at compile time:

- call non-`constexpr` functions (including most of `<cmath>` before C++26, I/O, and system calls);
- execute **undefined behaviour**: signed overflow, out-of-bounds access, reading uninitialized memory,
  dereferencing null;
- use `static` or `thread_local` local variables (relaxed in C++23), `goto`, or `reinterpret_cast`.

That UB rule is a powerful feature. The compiler acts as a perfect sanitizer for compile-time evaluation:

```cpp
constexpr std::int32_t bad = pow10_32(10);   // error: overflow in constant expression
```

> [!NOTE]
> "May" is the key word. `constexpr` on a function means "can be evaluated at compile time," not "will be." If
> you call it in a context that doesn't require a constant, the compiler is free to evaluate it at run time,
> though the optimizer often folds it anyway when the arguments are known.

### When does it run at compile time?

| Call site | Evaluated at |
|---|---|
| Initializing a `constexpr` variable, template argument, array bound, `static_assert`, `case` label | compile time (guaranteed) |
| Arguments not constant | run time |
| Arguments constant, result used in an ordinary expression | compiler's choice (usually folded at `-O2`) |

To *force* compile-time evaluation, use the result to initialize a `constexpr` variable, or make the function
`consteval`.

### Exercises

:::exercise Compile time or run time?
Given `constexpr int sq(int x) { return x * x; }`, which calls are guaranteed to be evaluated at compile time?
(a) `constexpr int a = sq(5);` (b) `int b = sq(5);` (c) `std::array<int, sq(3)> c;` (d) `int d = sq(argc);`
(e) `static_assert(sq(4) == 16);`
:::solution
(a), (c) and (e) are guaranteed: each context requires a constant expression. (b) isn't guaranteed (though at
`-O2` it will almost certainly be folded to `25`). (d) can't be: `argc` is only known at run time.
:::

---

## 3. `consteval` and `constinit`

C++20 added two keywords that tighten the guarantees.

**`consteval`** declares an **immediate function**: every call *must* be evaluated at compile time. A call with
run-time arguments is an error.

```cpp
consteval std::uint64_t symbol_key(std::string_view s) {
    if (s.size() > 8) throw "symbol too long";   // reaching a throw at compile time is a compile error
    std::uint64_t k = 0;
    for (char c : s) k = (k << 8) | static_cast<unsigned char>(c);
    return k;
}

switch (key) {
    case symbol_key("AAPL"): ...   // computed by the compiler
    case symbol_key("MSFT"): ...
}
```

Use `consteval` when run-time evaluation would be a mistake: a function that's only correct or affordable at
compile time, or that validates literals (like a format-string checker; `std::format` does exactly this).

**`constinit`** applies to variables with static storage duration. It requires the initializer to be a constant
expression, but unlike `constexpr` it **doesn't** make the variable `const`:

```cpp
constinit std::atomic<std::uint64_t> g_next_order_id{1};   // initialized at compile time, mutable at run time
```

Its purpose is to prevent the **static initialization order fiasco**: globals with dynamic initializers in
different translation units are initialized in an unspecified order, so one may read another before it's
initialized. `constinit` guarantees the value is in place before any code runs.

| Keyword | Applies to | Guarantees |
|---|---|---|
| `constexpr` variable | variables | compile-time value, and `const` |
| `constexpr` function | functions | *may* run at compile time |
| `consteval` | functions | *must* run at compile time |
| `constinit` | static/thread-local variables | compile-time initialization; still mutable |

---

## 4. Lookup tables computed by the compiler

The most common performance use of `constexpr` is precomputing tables. A function that returns a `std::array` can
fill it with any loop you like, and the result is stored in the binary's read-only data:

```cpp
constexpr auto kPow10 = [] {
    std::array<std::int64_t, 19> t{};
    t[0] = 1;
    for (std::size_t i = 1; i < t.size(); ++i) t[i] = t[i - 1] * 10;
    return t;
}();

static_assert(kPow10[0] == 1 && kPow10[18] == 1'000'000'000'000'000'000);

std::int64_t to_ticks(std::int64_t mantissa, int decimals, int tick_decimals) {
    return mantissa * kPow10[tick_decimals - decimals];   // one load instead of a loop
}
```

The immediately invoked lambda (`[] { ... }()`) is a handy way to run a block of code to initialize a
`constexpr` variable.

More examples from trading systems:

- A 256-entry table classifying bytes for a FIX parser (digit, delimiter, `=`, other).
- A table of `log` or `exp` values at fixed points for a fast approximation.
- Byte offsets of each field in a binary message, derived from a field list.
- A CRC or hash table.

```cpp
constexpr auto kIsDigit = [] {
    std::array<bool, 256> t{};
    for (int c = '0'; c <= '9'; ++c) t[c] = true;
    return t;
}();
```

> [!PERF]
> A table lookup is a load: about 1 ns if the table is in L1, more if it isn't. A table only beats computation
> when the computation is expensive (division, `pow`, a loop) **and** the table stays hot in cache. A 256-byte
> table is nearly free; a 1 MB table can be slower than recomputing. Measure (Chapter 6.1).

### Validating data at compile time

`static_assert` with a `constexpr` function lets you check configuration tables when the code is built, instead of
discovering errors in production:

```cpp
struct Venue { std::string_view name; std::int64_t tick_size; std::int32_t lot_size; };

constexpr std::array kVenues{
    Venue{"XNAS", 1, 100},
    Venue{"XNYS", 1, 100},
    Venue{"XLON", 5, 1},
};

constexpr bool venues_valid() {
    for (const auto& v : kVenues)
        if (v.tick_size <= 0 || v.lot_size <= 0 || v.name.size() != 4) return false;
    return true;
}
static_assert(venues_valid(), "bad venue table");
```

### Exercises

:::exercise A digit table
Write a `constexpr` table `kDigitValue` of 256 `std::int8_t`s where `kDigitValue['0'..'9']` is 0–9 and every other
entry is -1. Then write `constexpr int parse_digit(char c)` using it, and `static_assert` two cases.
:::solution
```cpp
constexpr auto kDigitValue = [] {
    std::array<std::int8_t, 256> t{};
    t.fill(-1);
    for (int c = '0'; c <= '9'; ++c) t[c] = static_cast<std::int8_t>(c - '0');
    return t;
}();

constexpr int parse_digit(char c) { return kDigitValue[static_cast<unsigned char>(c)]; }

static_assert(parse_digit('7') == 7);
static_assert(parse_digit('x') == -1);
```
The `static_cast<unsigned char>` matters: `char` is signed on x86, so a byte ≥ 128 would otherwise produce a
negative index. At compile time that's a hard error; at run time, an out-of-bounds read.
:::

---

## 5. `if constexpr` and `static_assert`

`if constexpr` chooses a branch **at compile time**. The discarded branch isn't instantiated, so it can contain
code that wouldn't compile for the current template arguments:

```cpp
template <class T>
std::int64_t to_ticks(T price, std::int64_t tick_size) {
    if constexpr (std::is_integral_v<T>) {
        return price / tick_size;                        // integer price in minimum units
    } else {
        static_assert(std::is_floating_point_v<T>, "price must be integral or floating point");
        return std::llround(price / static_cast<T>(tick_size));
    }
}
```

With a plain `if`, both branches must compile for every `T`, and `std::llround` on an integer type would at best
be wasteful. `if constexpr` lets one function template adapt to its argument type with no run-time branch.

`static_assert(condition, message)` stops compilation with your message if the condition is false. Use it to
document and enforce assumptions:

```cpp
static_assert(sizeof(Order) == 32, "Order layout changed: update the wire codec");
static_assert(std::is_trivially_copyable_v<Quote>, "Quote is memcpy'd into shared memory");
static_assert(std::endian::native == std::endian::little);
```

Each of these turns a silent, run-time disaster into a compile error at the moment someone breaks the assumption.

### Exercises

:::exercise Why `if constexpr`?
This function fails to compile when called with `T = int`. Why, and how does `if constexpr` fix it?

```cpp
template <class T>
std::string describe(const T& v) {
    if (std::is_arithmetic_v<T>) return std::to_string(v);
    else return v.name();
}
```
:::solution
With a plain `if`, both branches are compiled for every `T`. For `T = int`, `v.name()` is ill-formed (`int` has no
members), so the instantiation fails even though that branch would never run. With `if constexpr`, the discarded
branch isn't instantiated, so each `T` only needs to support the branch that's selected for it.
:::

---

## Summary

- `constexpr` variables are compile-time constants with types and scope. Prefer them to macros and plain `const`.
- `constexpr` functions *can* run at compile time; they're guaranteed to only where a constant is required. Any UB
  during compile-time evaluation is a compile error.
- `consteval` forces compile-time evaluation; `constinit` guarantees static initialization of a mutable global.
- Build lookup tables at compile time with a `constexpr` lambda returning a `std::array`, but keep tables small
  enough to stay in cache.
- Use `static_assert` to turn layout, size and configuration assumptions into compile errors.
- `if constexpr` discards the untaken branch, letting one template adapt to different types with no run-time cost.

## Key terms

**Constant expression** · **`constexpr` variable** · **`constexpr` function** · **`consteval`** ·
**immediate function** · **`constinit`** · **static initialization order fiasco** · **lookup table** ·
**`static_assert`** · **`if constexpr`**
