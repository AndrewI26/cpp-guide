+++
title = "How C++ Becomes Machine Code"
minutes = 35
practice_tags = ["toolchain", "inline"]
+++

## Why this chapter matters

Imagine your first week on a trading desk's C++ team. Three things happen:

1. You add one line to `price_utils.hpp`, and the rebuild takes eleven minutes, because 400 files included it.
2. You move a helper function into a header, and the linker fails with `multiple definition of 'mid_price(double, double)'`.
3. A senior engineer profiles your feed handler and points out that a two-instruction getter is being *called*
   millions of times per second instead of inlined, because its definition is in a `.cpp` file the optimizer
   couldn't see.

All three come from the same place: **the C++ compilation model**. C++ compiles each source file separately and
then stitches the results together. That design is decades old, and it explains build times, a whole category of
confusing errors, and (most important for us) *what the optimizer can and cannot see*. You can't reason about
performance without it, so it comes first.

By the end of this chapter you should be able to:

- name the four stages of a build and run each one by hand;
- explain the difference between a declaration and a definition, and what the linker does with each;
- diagnose "undefined reference" and "multiple definition" errors;
- state the One Definition Rule and use `inline` correctly;
- explain why where a function is *defined* affects how fast it runs.

---

## 1. The four stages of a build

When you type `g++ main.cpp -o main`, it looks like one step. It's actually four separate programs run one after
another:

| Stage | Input | Output | Tool |
|---|---|---|---|
| Preprocessing | `main.cpp` | one big expanded source file | `cpp` (the C preprocessor) |
| Compilation | expanded source | assembly (`main.s`) | `cc1plus` |
| Assembly | `main.s` | object file (`main.o`) | `as` |
| Linking | one or more `.o` files plus libraries | executable | `ld` |

You can stop the pipeline after any stage and look at the result:

```bash
g++ -std=c++20 -E main.cpp -o main.ii   # stop after preprocessing
g++ -std=c++20 -S main.cpp -o main.s    # stop after compilation (assembly text)
g++ -std=c++20 -c main.cpp -o main.o    # stop after assembly (object file)
g++ main.o -o main                      # link only
```

```svg "The build pipeline. Every source file goes through the first three stages alone; only the linker sees the whole program."
<svg viewBox="0 0 720 225" xmlns="http://www.w3.org/2000/svg" role="img"><defs><marker id="arrowhead" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path class="head" d="M0,0 L10,5 L0,10 z"/></marker></defs><text class="muted" x="50" y="22" text-anchor="middle">source</text><text class="muted" x="190" y="22" text-anchor="middle">preprocessed (-E)</text><text class="muted" x="330" y="22" text-anchor="middle">assembly (-S)</text><text class="muted" x="470" y="22" text-anchor="middle">object (-c)</text><text class="muted" x="595" y="22" text-anchor="middle">link</text><text class="muted" x="681" y="22" text-anchor="middle">output</text><rect class="box-strong" x="10" y="45" width="80" height="40" rx="8"/><text class="mono" x="50.0" y="69.0" text-anchor="middle">main.cpp</text><path class="arrow" d="M92,65 L148,65"/><rect class="box" x="150" y="45" width="80" height="40" rx="8"/><text class="mono" x="190.0" y="69.0" text-anchor="middle">main.ii</text><path class="arrow" d="M232,65 L288,65"/><rect class="box" x="290" y="45" width="80" height="40" rx="8"/><text class="mono" x="330.0" y="69.0" text-anchor="middle">main.s</text><path class="arrow" d="M372,65 L428,65"/><rect class="box" x="430" y="45" width="80" height="40" rx="8"/><text class="mono" x="470.0" y="69.0" text-anchor="middle">main.o</text><rect class="box-strong" x="10" y="135" width="80" height="40" rx="8"/><text class="mono" x="50.0" y="159.0" text-anchor="middle">anal….cpp</text><path class="arrow" d="M92,155 L148,155"/><rect class="box" x="150" y="135" width="80" height="40" rx="8"/><text class="mono" x="190.0" y="159.0" text-anchor="middle">anal….ii</text><path class="arrow" d="M232,155 L288,155"/><rect class="box" x="290" y="135" width="80" height="40" rx="8"/><text class="mono" x="330.0" y="159.0" text-anchor="middle">anal….s</text><path class="arrow" d="M372,155 L428,155"/><rect class="box" x="430" y="135" width="80" height="40" rx="8"/><text class="mono" x="470.0" y="159.0" text-anchor="middle">anal….o</text><rect class="box-strong" x="560" y="85" width="70" height="70" rx="8"/><text class="mono" x="595.0" y="124.0" text-anchor="middle">ld</text><path class="arrow" d="M512,65 C540,65 540,105 558,105"/><path class="arrow" d="M512,155 C540,155 540,135 558,135"/><rect class="box-strong" x="650" y="100" width="62" height="40" rx="8"/><text class="mono" x="681.0" y="124.0" text-anchor="middle">app</text><path class="arrow" d="M632,120 L648,120"/><text class="muted" x="270" y="210" text-anchor="middle">each .cpp is a separate translation unit, compiled with no knowledge of the others</text></svg>
```


Try it on a tiny program:

```cpp main.cpp
#include <cstdio>

int square(int x) { return x * x; }

int main() {
    std::printf("%d\n", square(7));
}
```

The preprocessed file `main.ii` is usually **tens of thousands of lines long**, almost all of it from `<cstdio>`.
That's the first lesson: a "small" source file is not small once headers are pasted in. Every one of those lines
gets parsed again in every file that includes the header.

> [!NOTE]
> The unit the compiler works on, one `.cpp` file after preprocessing, is called a **translation unit (TU)**.
> The compiler sees exactly one TU at a time and knows nothing about the others.

### Exercises

:::exercise Running the stages
Using the `main.cpp` above, produce `main.ii`, `main.s` and `main.o`. Roughly how many lines is `main.ii`?
Find `square` in `main.s`. What does its name look like, and why?
:::hint
Try `wc -l main.ii` and `grep square main.s`.
:::solution
On a typical Linux system `main.ii` is a few hundred to a few thousand lines for `<cstdio>`. A header like
`<iostream>` produces tens of thousands. In `main.s` the function shows up as something like `_Z6squarei`.
That's a **mangled name**: the compiler encodes the parameter types (`i` = `int`) into the symbol so that
overloads like `square(int)` and `square(double)` get different symbols. Section 4 covers this.
:::

:::exercise Which stage?
For each error, say which stage produces it: (a) `fatal error: prices.hpp: No such file or directory`;
(b) `error: expected ';' before '}' token`; (c) `undefined reference to 'vwap(...)'`.
:::solution
(a) Preprocessing: `#include` couldn't find the file. (b) Compilation: a syntax error in the expanded source.
(c) Linking: the code compiled fine, but no object file provided a definition for `vwap`.
:::

---

## 2. The preprocessor: copy, paste, and macros

The preprocessor is a text-substitution engine that runs before the compiler proper. It doesn't understand C++
at all. Its main directives:

- `#include "file"` / `#include <file>`: **paste the whole file here**, literally.
- `#define NAME value`: textual replacement of `NAME` with `value` from here on.
- `#if` / `#ifdef` / `#ifndef` / `#endif`: conditionally keep or drop text.

Because `#include` is a literal paste, including the same header twice (often indirectly) would paste its
definitions twice. Every header therefore protects itself with an **include guard**:

```cpp order.hpp
#pragma once        // non-standard but supported by every major compiler

struct Order {
    long id;
    double price;
    int qty;
};
```

The traditional, fully portable form is:

```cpp
#ifndef ORDER_HPP
#define ORDER_HPP
// ... contents ...
#endif
```

### Why macros are dangerous

Macros are pure text substitution, so they ignore scope, types and evaluation rules:

```cpp
#define SQUARE(x) x * x

int a = SQUARE(1 + 2);   // expands to 1 + 2 * 1 + 2  ==  5, not 9
int b = SQUARE(i++);     // expands to i++ * i++      -> undefined behaviour
```

Macros also have no scope: a macro rewrites *every* later matching token in the translation unit, in your code
and in every header included after it. The classic example is `<windows.h>`, which defines macros named `min` and
`max`. After including it, `std::max(a, b)` and `std::numeric_limits<int>::max()` stop compiling, because the
preprocessor rewrites them before the compiler sees them. And because the compiler never sees
the macro, error messages and debuggers show you the expanded mess rather than what you wrote.

Older C and C++ codebases (and plenty of trading systems written in the 2000s) are full of macros, because for a
long time they were the only way to get constants, generic code and zero-overhead "functions". **Modern C++ has a
language feature for nearly every one of those jobs.** The replacements are type-checked, scoped and debuggable,
and they cost nothing at run time.

### Modern replacements for macros

#### 1. Constants → `constexpr` variables and `enum class`

```cpp
// Old style
#define TICK_SIZE   0.01
#define MAX_LEVELS  1024
#define SIDE_BUY    0
#define SIDE_SELL   1

// Modern
inline constexpr double kTickSize  = 0.01;    // typed, scoped, visible in the debugger
inline constexpr int    kMaxLevels = 1024;

enum class Side : std::uint8_t { Buy, Sell };   // a real type: can't be mixed up with an int
```

A `constexpr` variable is a compile-time constant, so `std::array<Level, kMaxLevels>` works exactly as the macro
did. Unlike the macro, it has a type (`0.01` vs `0.01f` can't silently differ), it lives in a namespace, and a
typo gives a clear error. `inline` (C++17) lets you define it in a header without ODR problems (section 5).

`enum class` replaces families of `#define` integer codes. `void send(Side s)` can't be called with `send(7)` or
with an unrelated `OrderType`, which the macro version happily accepts.

#### 2. Function-like macros → `constexpr` functions

```cpp
// Old style
#define SQUARE(x)        ((x) * (x))
#define TO_TICKS(px, t)  ((long)((px) / (t) + 0.5))

// Modern
constexpr double square(double x) { return x * x; }

constexpr std::int64_t to_ticks(double px, double tick) {
    return static_cast<std::int64_t>(px / tick + (px >= 0 ? 0.5 : -0.5));
}

static_assert(to_ticks(101.25, 0.25) == 405);     // evaluated by the compiler
```

Arguments are evaluated exactly once, so `square(next_price())` calls the function once and `square(i++)` is
well defined. The function respects types and namespaces. And it's just as fast: a small function whose body is
visible in a header is inlined by the optimizer, producing the same instructions as the macro. A `constexpr`
function can also run at compile time whenever its arguments are constants.

If a function must **only** run at compile time, for example to build a lookup table that must never be computed
on the hot path, mark it `consteval` (C++20):

```cpp
consteval std::array<std::uint64_t, 20> make_pow10() {
    std::array<std::uint64_t, 20> t{};
    std::uint64_t v = 1;
    for (auto& e : t) { e = v; v *= 10; }
    return t;
}
inline constexpr auto kPow10 = make_pow10();      // computed while compiling, stored as read-only data
```

#### 3. Type-generic macros → templates and concepts

```cpp
// Old style: works on any type, including ones where it makes no sense, and evaluates twice
#define MAX(a, b) ((a) > (b) ? (a) : (b))

// Modern: one template, constrained to types that can actually be compared
#include <concepts>

template <std::totally_ordered T>
constexpr const T& max_of(const T& a, const T& b) { return a < b ? b : a; }

// C++20 abbreviated syntax for the same idea:
constexpr auto clamp_qty(std::integral auto q, std::integral auto lo, std::integral auto hi) {
    return q < lo ? lo : (q > hi ? hi : q);
}
```

The template generates one specialized function per type used, exactly like the macro generates code at each
use, but with type checking. **Concepts** (`std::totally_ordered`, `std::integral`) turn a page of template errors
into one line: "`Order` does not satisfy `totally_ordered`". (For `max` specifically, just use `std::max`.)

#### 4. `#ifdef` code paths → `if constexpr`

Macros were the traditional way to choose code at compile time: `#if __BYTE_ORDER__ == ...`, `#ifdef DEBUG`.
`if constexpr` makes the same decision in the language itself, and the discarded branch generates no code:

```cpp
#include <bit>
#include <cstring>

// Read a little-endian u32 from the wire, on any machine.
inline std::uint32_t load_le32(const unsigned char* p) {
    std::uint32_t v;
    std::memcpy(&v, p, sizeof v);
    if constexpr (std::endian::native == std::endian::big)   // C++20 <bit>: no platform macros
        v = __builtin_bswap32(v);
    return v;
}

inline constexpr bool kDebugChecks = false;   // flip in a debug build config

inline void apply_fill(std::int64_t& position, std::int64_t qty) {
    if constexpr (kDebugChecks) {
        // expensive validation: compiled and type-checked, but removed entirely when false
    }
    position += qty;
}
```

There's a subtle advantage over `#ifdef DEBUG ... #endif`: code inside a false `if constexpr` is still **parsed
and checked by the compiler**. Code hidden behind an `#ifdef` that nobody compiles rots silently. It stops
compiling, and you only find out on the day you need the debug build.

`if constexpr` also works on types, which replaces a lot of macro-based "generic" code:

```cpp
template <class T>
void write_field(char* out, const T& value) {
    if constexpr (std::is_integral_v<T>)          { /* fast integer path   */ }
    else if constexpr (std::is_floating_point_v<T>) { /* fixed-point path  */ }
    else static_assert(sizeof(T) == 0, "write_field: unsupported type");
}
```

#### 5. `#error` checks → `static_assert`

```cpp
// Old style
#if !defined(__x86_64__)
#error "This code assumes x86-64"
#endif

// Modern: check the property you actually depend on
struct OrderHot { std::int64_t price; std::int32_t qty; std::int32_t next; };
static_assert(sizeof(OrderHot) == 16, "OrderHot must stay 16 bytes: 4 per cache line");
static_assert(std::is_trivially_copyable_v<OrderHot>, "OrderHot is memcpy'd into shared memory");
static_assert(sizeof(void*) == 8, "64-bit only");
```

`static_assert` understands types, `sizeof`, `alignof` and any `constexpr` expression, which the preprocessor
can't (it runs before types exist). Pinning the sizes of hot structs like this is common practice in low-latency
code. If someone adds a field, the build breaks instead of the cache footprint silently doubling.

#### 6. `__FILE__` / `__LINE__` logging macros → `std::source_location`

Logging and assertion macros existed largely to capture the caller's file and line. C++20 gives you that in an
ordinary function, through a default argument:

```cpp
#include <source_location>
#include <cstdio>

void log_warn(const char* msg, std::source_location loc = std::source_location::current()) {
    std::fprintf(stderr, "%s:%u [%s] %s\n", loc.file_name(), unsigned(loc.line()), loc.function_name(), msg);
}

void on_reject(int reason) {
    if (reason != 0) log_warn("order rejected");   // reports on_reject's file and line, not log_warn's
}
```

The default argument is evaluated **at the call site**, so `loc` describes the caller.

#### 7. Code-generating macros → variadic templates and fold expressions

Macros were often used to stamp out repetitive code: one line per field, unrolled loops, "apply to every
argument". Variadic templates and fold expressions (C++17) do this with type safety:

```cpp
// Is every quantity positive? Works for any number of arguments, of any integer types.
constexpr bool all_positive(std::integral auto... qty) { return ((qty > 0) && ...); }

static_assert(all_positive(1, 2L, 3));
static_assert(!all_positive(5, 0));

// Sum notional across several books without writing the loop by hand
template <class... Books>
double total_notional(const Books&... books) { return (books.notional() + ... + 0.0); }
```

For a snippet repeated inside a single function, a **lambda** is usually the right replacement for a local
macro: `auto emit = [&](Side s, std::int64_t px) { ... };`.

#### 8. Compiler-hint macros → standard attributes

Codebases are full of macros like `LIKELY(x)`, `UNUSED(x)` and `DEPRECATED`, each wrapping a compiler-specific
extension. Most now have standard spellings:

```cpp
if (seq != expected) [[unlikely]] {         // was: if (UNLIKELY(seq != expected))
    request_retransmit(expected);
}

[[nodiscard]] bool try_send(const Order&);  // warn if the caller ignores the result
[[deprecated("use try_send")]] void send(const Order&);
void handler([[maybe_unused]] int fd);      // was: UNUSED(fd)
[[gnu::always_inline]] inline int fast_path(int x) { return x + 1; }  // still compiler-specific, but no macro needed
```

### Summary: what to use instead

| Old macro use | Modern replacement | Since |
|---|---|---|
| `#define kConst 42` | `inline constexpr int kConst = 42;` | C++11/17 |
| Integer code families | `enum class` | C++11 |
| `#define SQUARE(x) ...` | `constexpr` function (`consteval` to force compile time) | C++11/20 |
| `#define MAX(a, b) ...` | function template + concepts | C++20 |
| `#if` / `#ifdef` code paths | `if constexpr`, `std::endian`, type traits | C++17/20 |
| `#error` | `static_assert` | C++11 |
| `__FILE__`, `__LINE__` in logging | `std::source_location` | C++20 |
| Repetition / code generation | variadic templates, fold expressions, lambdas | C++11/17 |
| `LIKELY`, `UNUSED`, `DEPRECATED` | `[[likely]]`, `[[maybe_unused]]`, `[[deprecated]]` | C++14/17/20 |
| Include guards | `#pragma once` today, **modules** (`import`) in C++20 | C++20 |

### What macros are still for

A few jobs genuinely need the preprocessor, because they happen before or outside the language:

- **Include guards** (`#pragma once`), until your toolchain and build system fully support C++20 modules.
- **Platform and compiler detection** where no standard query exists: `#ifdef __linux__`, `#if defined(__clang__)`.
- **Feature-test macros** to adapt to the standard library you're compiled against:
  `#if __cpp_lib_expected >= 202202L`.
- **Build configuration** passed by the build system, like `-DNDEBUG` or `-DENABLE_TRACING=1`. Even then, a
  common pattern is to convert it once into a `constexpr bool` and use `if constexpr` everywhere else.
- **Stringizing** source text (`#expr`), which is why `assert` and test macros like `CHECK(a == b)` are still
  macros: only the preprocessor can turn the expression itself into a string for the error message.

The rule of thumb: **if the language can do it, use the language.** Reach for a macro only when you need
something that exists before the compiler sees the code.

> [!PERF]
> Header bloat is the main cause of slow C++ builds. Include what you use, prefer forward declarations
> (`struct Order;`) in headers when you only need a pointer or reference, and keep heavy headers like `<iostream>`
> and `<regex>` out of widely-included headers.

### Exercises

:::exercise Macro expansion
What does `SQUARE(a - b) / SQUARE(c)` expand to with the macro above? Fix the macro so it behaves, then explain
why a `constexpr` function is still better.
:::solution
It expands to `a - b * a - b / c * c`, which is nothing like what was meant. A fixed macro wraps every argument
use and the whole result in parentheses: `#define SQUARE(x) ((x) * (x))`. It still evaluates `x` twice, so
`SQUARE(next_price())` calls the function twice and `SQUARE(i++)` is still undefined behaviour. The function
version has none of these problems, respects namespaces, is type-checked, and costs nothing at run time once
inlined:

```cpp
constexpr int square(int x) { return x * x; }
```
:::

:::exercise Modernize the macros
Rewrite each of these without macros, and say what bug or risk the rewrite removes:

```cpp
#define NOTIONAL(px, qty)  px * qty
#define BUY 1
#define SELL 2
#define ABS(x) ((x) < 0 ? -(x) : (x))
```
:::hint
Try `NOTIONAL(a + b, q)` with the original macro, and ask whether `send(BUY)` and `send(3)` should both compile.
:::solution
```cpp
constexpr std::int64_t notional(std::int64_t px_ticks, std::int64_t qty) { return px_ticks * qty; }

enum class Side : std::uint8_t { Buy = 1, Sell = 2 };

template <class T>
constexpr T abs_of(T x) { return x < 0 ? -x : x; }
```

- `NOTIONAL(a + b, q)` expands to `a + b * q`. The function takes its arguments as values, so precedence can't
  break it, and it has explicit types.
- `BUY` and `SELL` are plain integers, so nothing stops `send(3)`, or passing an order *type* where a *side* is
  expected. `enum class Side` makes those compile errors.
- `ABS(next_qty())` evaluates `next_qty()` twice (two calls, possibly two different values). The template
  evaluates its argument once. (Or just use `std::abs`.)

All three versions compile to the same instructions as the macros once inlined.
:::

:::exercise #ifdef vs if constexpr
A codebase guards expensive validation with `#ifdef DEBUG_CHECKS ... #endif`. Nobody builds with
`DEBUG_CHECKS` for six months. What typically happens, and how does
`if constexpr (kDebugChecks)` avoid it?
:::solution
The guarded code **rots**. Meanwhile fields get renamed, functions change signature and headers move, but the
code inside the `#ifdef` is never compiled, so nobody notices. The day someone needs the checks to chase a
production bug, the debug build doesn't compile. With `if constexpr (kDebugChecks)`, the compiler still parses
and type-checks the discarded branch (for non-template code) on every build, so it keeps compiling. And because
the condition is a compile-time constant, the branch generates no code when it's false, so there's no run-time
cost.
:::

:::exercise A logger without macros
Write `void trace(std::string_view msg, std::source_location loc = std::source_location::current())`
that prints `file:line msg`. Then explain why the default argument must be on the *declaration* the caller sees,
rather than calling `std::source_location::current()` inside the function body.
:::solution
```cpp
#include <cstdio>
#include <source_location>
#include <string_view>

inline void trace(std::string_view msg, std::source_location loc = std::source_location::current()) {
    std::printf("%s:%u %.*s\n", loc.file_name(), unsigned(loc.line()), int(msg.size()), msg.data());
}
```

Default arguments are evaluated **at the call site**, so `current()` captures the caller's file and line. If you
called `std::source_location::current()` inside the body, it would always report the line *inside `trace`*,
which is useless. That's exactly why logging used to require a macro, and why `source_location` is designed to
be used as a default argument.
:::

:::exercise Why include guards?
`book.hpp` includes `order.hpp`. `main.cpp` includes both `order.hpp` and `book.hpp`. Without include guards,
what error do you get and why?
:::solution
`struct Order` is pasted into `main.cpp`'s translation unit twice, so the compiler reports
`redefinition of 'struct Order'`. A class can be *defined* only once per translation unit. With `#pragma once`
the second paste is skipped.
:::

---

## 3. Declarations, definitions, and translation units

The distinction that makes separate compilation work:

- A **declaration** introduces a name and its type: "there is a function `vwap` taking these arguments and
  returning `double`." You can repeat it as often as you like.
- A **definition** provides the thing itself: the function body, or the storage for a variable.

```cpp
double vwap(const double* px, const int* qty, int n);   // declaration
extern int g_order_count;                              // declaration of a variable

double vwap(const double* px, const int* qty, int n) { // definition
    double notional = 0; long volume = 0;
    for (int i = 0; i < n; ++i) { notional += px[i] * qty[i]; volume += qty[i]; }
    return volume ? notional / volume : 0.0;
}
int g_order_count = 0;                                 // definition
```

The usual project layout follows from this. A **header** (`.hpp`) holds declarations, and one **source file**
(`.cpp`) holds the definitions:

```cpp analytics.hpp
#pragma once
double vwap(const double* px, const int* qty, int n);
```

```cpp analytics.cpp
#include "analytics.hpp"
double vwap(const double* px, const int* qty, int n) { /* ... */ return 0; }
```

```cpp main.cpp
#include "analytics.hpp"
int main() { double px[] = {100.0, 101.0}; int q[] = {10, 30}; return vwap(px, q, 2) > 0 ? 0 : 1; }
```

When the compiler processes `main.cpp`, it sees only the declaration of `vwap`. That's enough to type-check the
call and emit a `call vwap` instruction with a placeholder address. Filling in the real address is left to the
linker.

### Exercises

:::exercise Declaration or definition?
Classify each line: (a) `int f(int);` (b) `int f(int x) { return x; }` (c) `struct Quote;`
(d) `struct Quote { double bid, ask; };` (e) `extern double g_tick;` (f) `double g_tick = 0.01;`
:::solution
(a) declaration (b) definition (c) declaration (a *forward declaration*, which makes `Quote` an incomplete type)
(d) definition of the class (e) declaration (f) definition.
:::

:::exercise What does the compiler need?
In a header you only pass `const Quote&` around and never touch its members. Can you get away with the forward
declaration `struct Quote;` instead of including `quote.hpp`? What if you store a `Quote` by value as a member?
:::solution
For references and pointers, yes: the compiler doesn't need to know the size or layout of `Quote`, so a forward
declaration is enough, and that file no longer depends on `quote.hpp` (faster builds, fewer recompiles). To store
a `Quote` **by value** the compiler needs its size, so you need the full definition and must include the header.
:::

---

## 4. Linking: symbols, undefined references, and name mangling

Each object file contains machine code plus a **symbol table**: the names it *defines* and the names it *needs*.
You can inspect it with `nm`:

```bash
$ g++ -c analytics.cpp main.cpp
$ nm -C analytics.o
0000000000000000 T vwap(double const*, int const*, int)
$ nm -C main.o
                 U vwap(double const*, int const*, int)
0000000000000000 T main
```

`T` means "defined here, in the text (code) section" and `U` means "undefined: needed from elsewhere". The
linker's job is to match every `U` with exactly one `T` across all the object files and libraries, then patch in
the addresses.

```svg "Symbol resolution: main.o needs vwap, analytics.o provides it."
<svg viewBox="0 0 660 175" xmlns="http://www.w3.org/2000/svg" role="img"><defs><marker id="arrowhead" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path class="head" d="M0,0 L10,5 L0,10 z"/></marker></defs><rect class="box" x="30" y="20" width="230" height="120" rx="10"/><text class="title" x="50" y="48">main.o</text><text class="mono" x="50" y="82"><tspan class="t-green">T</tspan>  main</text><text class="mono" x="50" y="112"><tspan class="t-red">U</tspan>  vwap(double const*, …)</text><rect class="box" x="400" y="20" width="230" height="120" rx="10"/><text class="title" x="420" y="48">analytics.o</text><text class="mono" x="420" y="82"><tspan class="t-green">T</tspan>  vwap(double const*, …)</text><path class="arrow" d="M262,108 C330,108 330,78 398,78"/><text class="muted" x="330" y="160" text-anchor="middle">the linker matches every U (needed) with exactly one T (defined)</text></svg>
```


The two classic link errors are the two ways that matching can fail:

| Error | Meaning | Usual cause |
|---|---|---|
| `undefined reference to 'f'` | some `U` has no matching definition | forgot to compile/link a `.cpp`, signature mismatch, missing library (`-lpthread`) |
| `multiple definition of 'f'` | two object files both define `f` | a non-`inline` function or variable *defined* in a header included by two `.cpp` files |

### Name mangling

C++ allows overloading, so the symbol for a function has to encode its parameter types. `vwap(double const*, int
const*, int)` becomes `_Z4vwapPKdPKii`. `nm -C` and `c++filt` demangle these names back to something readable.
If the declaration in the header and the definition disagree, even just `int` vs `long`, the mangled names differ
and you get an undefined reference, not a type error.

To call C code (or be called from C, Python's `ctypes`, and so on), turn mangling off with `extern "C"`:

```cpp
extern "C" double price_option(double spot, double strike, double vol, double t);
```

### Exercises

:::exercise Diagnose the link error
`analytics.hpp` declares `double vwap(const double*, const int*, int);` but `analytics.cpp` defines
`double vwap(const double* px, const long* qty, int n) {...}`. Everything compiles. What error do you get, and
at which stage?
:::solution
A **link** error: `undefined reference to 'vwap(double const*, int const*, int)'`. The definition is a different
overload (`long const*`) with a different mangled name, so the symbol `main.o` needs is never defined. Including
`analytics.hpp` at the top of `analytics.cpp` doesn't catch this, because the compiler treats the two as separate
overloads. This is one reason to keep header declarations and definitions in sync, and to turn on
`-Wmissing-declarations`, which warns when you define a non-static function with no prior declaration.
:::

:::exercise Read the symbol table
In the output of `nm -C some.o` you see `U operator new(unsigned long)` and `U std::chrono::_V2::steady_clock::now()`.
What does this tell you about the code in that file?
:::solution
The file calls `operator new` (it heap-allocates: a `new` expression, or a container like `std::vector` growing)
and reads `std::chrono::steady_clock::now()`. Both are defined in the C++ runtime library, which the linker pulls in
automatically. Scanning hot-path object files for unexpected `operator new` symbols is a real technique for
catching accidental allocations.
:::

---

## 5. The One Definition Rule and `inline`

The rule behind "multiple definition" errors is the **One Definition Rule (ODR)**:

1. Within one translation unit, each variable, function, class, and so on may be defined at most once.
2. Across the whole program, each non-inline function or variable that is used must be defined **exactly once**.
3. Classes, templates, and `inline` functions/variables may be defined in multiple translation units, but every
   definition must be **token-for-token identical**.

Rule 3 is what lets you put class definitions and templates in headers. To put an ordinary function's definition
in a header, mark it `inline`:

```cpp price_utils.hpp
#pragma once

inline double mid_price(double bid, double ask) {   // OK in a header
    return 0.5 * (bid + ask);
}

struct Quote {
    double bid, ask;
    double spread() const { return ask - bid; }      // implicitly inline (defined in class)
};

template <class T>
T clamp_qty(T q, T lo, T hi) { return q < lo ? lo : q > hi ? hi : q; }   // templates: OK

inline constexpr double kTickSize = 0.01;            // C++17 inline variable
```

> [!IMPORTANT]
> In modern C++, `inline` means **"multiple identical definitions are allowed"**. It does *not* mean
> "please inline this call". The optimizer decides about inlining on its own, though having the definition
> visible is what *lets* it inline (next section).

> [!WARNING]
> Violating rule 3 (two different definitions of the same inline function or class in different TUs, say because
> of a `#define` that differs between them) is **undefined behaviour with no diagnostic required**. The linker
> silently keeps one of them. These bugs are rare and miserable to find.

### Exercises

:::exercise Fix the header
`fees.hpp` contains `double fee(int qty) { return qty * 0.0005; }` and is included by `orders.cpp` and
`risk.cpp`. What goes wrong, and what are two ways to fix it?
:::solution
Both object files define the symbol `fee(int)`, so you get a *multiple definition* link error. Fix 1: mark it
`inline` (or `constexpr`, which implies `inline` for functions), so the definition can live in the header.
Fix 2: leave only the declaration `double fee(int qty);` in the header and move the definition into one
`fees.cpp`. Fix 1 lets callers inline the function; fix 2 hides the implementation and speeds up builds.
:::

:::exercise ODR and static
What happens if you write `static double fee(int qty) {...}` in the header instead of `inline`? Is it a good idea?
:::solution
`static` at namespace scope gives the function **internal linkage**: every TU that includes the header gets its
own private copy, so there is no link error. But you end up with duplicate copies of the code in the binary
(bigger code, worse instruction-cache use), and any `static` local variables inside are duplicated too, which can
be a real bug. `inline` is almost always the right choice for header functions. Use `static`, or better an
unnamed `namespace { }`, for helpers that live in one `.cpp` file.
:::

---

## 6. Why this matters for performance

The compiler optimizes **one translation unit at a time**. When it compiles `main.cpp` and sees a call to
`vwap(...)` whose body is in `analytics.cpp`, it can't inline it, can't constant-fold through it, can't vectorize
across it, and can't assume it has no side effects. The result is a real `call` instruction: saving registers,
jumping, returning, and often reloading memory afterwards because the call might have changed it.

For a large function that cost doesn't matter. For a tiny function called inside a hot loop, such as
`Quote::spread()`, `price_to_ticks()` or `is_buy()`, it can easily dominate. That's why performance-sensitive C++
puts small, hot functions **in headers** (inline or templates), where every caller can see the body.

There are two other ways to give the optimizer cross-TU visibility:

- **Link-Time Optimization (LTO)**: `-flto` stores the compiler's intermediate representation in the object files
  and runs the optimizer again over the whole program at link time. It's often a free 5–15% gain, at the cost of
  slower links. Chapter 8.4 covers it.
- **Unity builds**: concatenate many `.cpp` files into one TU. This is faster to build from scratch and also gives
  the optimizer more to see.

> [!QUANT]
> Low-latency trading codebases are often header-heavy or even header-only for the hot path (book updates,
> parsing, signal calculation), with `.cpp` files kept for cold code like configuration, logging setup and
> reporting. Now you know why.

### Worked example: measuring the cost of an invisible function

The project's `examples/inlining/` folder has a minimal version of this situation. `Quote` has two identical
getters. One is defined in the header; the other is defined in `quote.cpp`:

```cpp quote.hpp
struct Quote {
    double bid, ask;
    double spread_inline() const { return ask - bid; }   // body visible to every caller
    double spread_outofline() const;                    // body hidden in quote.cpp
};
```

`main.cpp` sums the spread over a million quotes with each version:

```cpp
double s = 0;
for (auto& x : q) s += x.spread_outofline();   // vs. x.spread_inline()
```

```bash
$ g++ -std=c++20 -O2 main.cpp quote.cpp -o inl && ./inl
1M quotes  out-of-line: 3.996 ms   inline: 1.813 ms   (2.2x)
$ g++ -std=c++20 -O2 -flto main.cpp quote.cpp -o inl_lto && ./inl_lto
1M quotes  out-of-line: 1.813 ms   inline: 1.812 ms   (1.0x)
```

Three things to notice:

1. The *only* difference is where the function body lives, and it costs 2.2×. Looking at `main.s` confirms it:
   the slow loop contains `call _ZNK5Quote16spread_outoflineEv` while the fast loop is just `movsd`, `subsd`,
   `addsd`.
2. With **`-flto`** the gap disappears completely, because the linker can now see into `quote.cpp` and inline
   the call.
3. The inlined loop isn't 10× faster, because it's limited by the chain of dependent `addsd` instructions
   (each addition has to wait for the previous one). Without `-ffast-math` the compiler isn't allowed to
   reorder floating-point additions, so it can't vectorize the sum. You'll learn to see limits like this in
   Chapter 5.1.


### Exercises

:::exercise Predict the assembly
`spread()` is defined in `quote.cpp`, and `total_spread` lives in `main.cpp` and loops over a million quotes calling
`q[i].spread()`. Without LTO, what instruction must appear inside the loop in `main.s`? What changes if you move
the definition into the class body in `quote.hpp`?
:::solution
A `call` to the mangled `Quote::spread()` symbol, once per iteration, with all the overhead that comes with it.
Once the body is visible in the header, the optimizer inlines it: the loop becomes a load of `ask`, a load of
`bid`, a subtract and an add, and at `-O3` it may even be vectorized to process several quotes per instruction.
On the test machine the measured difference is **2.2×** (see the worked example above).
:::

:::exercise Trade-offs
List one advantage and one disadvantage of moving more code into headers.
:::solution
Advantage: the optimizer can inline and specialize the code at every call site, so it runs faster. Disadvantage:
longer compile times and more recompilation, since every TU that includes the header re-parses the code and must
be rebuilt whenever it changes. Implementation details also leak into the interface.
:::

---

## Summary

- A build is **preprocess → compile → assemble → link**. You can stop at any stage with `-E`, `-S` or `-c`.
- `#include` is a textual paste. Headers need include guards, and heavy headers slow builds.
- Prefer language features to macros: `constexpr`, `enum class`, templates and concepts, `if constexpr`,
  `static_assert`, `std::source_location` and attributes. Keep macros for include guards, platform detection,
  feature tests, build flags and stringizing.
- The compiler sees one **translation unit** at a time. Declarations are enough to call a function; the linker
  resolves the definitions.
- `undefined reference` means a missing definition; `multiple definition` means an ODR violation.
- `inline` means "identical definitions allowed in multiple TUs". It's what lets hot code live in headers.
- The optimizer can only inline what it can see: **header definitions or LTO** for hot paths.

## Key terms

**Translation unit** · **macro** · **`constexpr` / `consteval`** · **`if constexpr`** · **`static_assert`** ·
**declaration** · **definition** · **symbol** · **name mangling** · **linkage** · **One Definition Rule** ·
**inline** · **LTO**
