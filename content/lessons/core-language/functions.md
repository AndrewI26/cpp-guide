+++
title = "Functions, Overloading and Inlining"
minutes = 35
practice_tags = ["inline", "toolchain"]
+++

## Why this chapter matters

A function call looks free in source code. Sometimes it is: the compiler inlines it and it vanishes. Sometimes it
costs a few nanoseconds of spilling registers, jumping and returning. On a path with a 2 µs budget that runs a
few hundred calls deep, the difference matters. And it's decided by things you control: where the definition
lives, how arguments are passed, and whether the compiler can see through the call.

Functions are also where C++'s **overload resolution** lives, the rules that decide which of several same-named
functions a call means. Most of the time it picks what you expect. When it doesn't, you get a call to the wrong
function with no error, like `set_qty(100.5)` silently calling the `int` overload.

By the end of this chapter you should be able to:

- describe what a call costs on x86-64 Linux and which arguments travel in registers;
- predict which overload a call selects, and design overload sets that don't surprise callers;
- use default arguments, `[[nodiscard]]`, and `noexcept` appropriately;
- explain what `inline` means to the linker versus what inlining means to the optimizer.

---

## 1. What a function call costs

On x86-64 Linux, calls follow the **System V AMD64 ABI**. The essentials:

- The first six integer or pointer arguments go in registers `rdi, rsi, rdx, rcx, r8, r9`.
- The first eight floating-point arguments go in `xmm0`–`xmm7`.
- Return values come back in `rax` (and `rdx`) or `xmm0` (and `xmm1`).
- Structs of up to 16 bytes made of simple fields are split across registers. Larger structs, and any type with
  a non-trivial copy constructor or destructor, are passed via a pointer to a copy in memory.
- Some registers are **callee-saved** (`rbx, rbp, r12`–`r15`): a function that uses them must save and restore
  them. All others are **caller-saved**: if the caller needs their values after the call, it must spill them.

A non-inlined call therefore costs: setting up arguments, `call` (push return address, jump), the function's
prologue and epilogue, `ret`, and any register spills around it. On a modern core that's a few cycles when
everything is predicted and cached, which is usually negligible.

The real cost is what the call **prevents**. The optimizer can't see through a call to a function whose body it
doesn't have. It must assume the function reads and writes any memory reachable from globals or its arguments,
so it can't keep values in registers across the call, can't vectorize a loop containing it, and can't constant-
fold through it.

```cpp
double total = 0;
for (int i = 0; i < n; ++i)
    total += fee(prices[i]);   // if fee() is opaque: one call per element, no vectorization
                               // if fee() is inlined: maybe 4 elements per instruction
```

> [!PERF]
> The question to ask isn't "how much does a call cost?" but "can the compiler see the callee?". Chapter 1.1
> showed how translation units limit what the compiler sees; Chapter 8.1 covers inlining decisions in depth.

### Exercises

:::exercise Registers or memory?
How is each argument passed on x86-64 Linux? (a) `int64_t`; (b) `double`; (c)
`struct P { int64_t price; int64_t qty; }`; (d) `struct Q { int64_t a, b, c; }`; (e) `std::string`.
:::solution
(a) In an integer register (`rdi` if first). (b) In `xmm0` if it's the first floating-point argument. (c) In two
integer registers: 16 bytes of integers. (d) In memory: 24 bytes exceeds the 16-byte limit, so the caller makes a
copy on the stack. (e) Via a pointer to a temporary copy: `std::string` has a non-trivial copy constructor and
destructor, so it's never passed in registers, regardless of size.
:::

---

## 2. Declarations, signatures and `[[nodiscard]]`

A function's **signature** (name, parameter types, and for members, `const` and ref qualifiers) is what
distinguishes it from other functions. The return type is not part of it for overloading purposes.

Two attributes are worth putting on most function declarations in trading code:

```cpp
[[nodiscard]] bool try_send(const Order& o);              // warn if the result is ignored
[[nodiscard]] std::int64_t notional(std::int64_t px, std::int64_t qty) noexcept;
```

`[[nodiscard]]` makes the compiler warn when the caller throws away the return value. For anything returning an
error indication or a computed value with no side effects, ignoring the result is almost always a bug:

```cpp
try_send(order);           // warning: ignoring return value: was the order actually sent?
```

You can also mark a type `[[nodiscard]]`, so every function returning it gets the check automatically. That's a
good idea for error types like `std::expected`-style results (Chapter 2.7).

`noexcept` promises the function won't throw. We'll see in Chapter 2.3 why it matters enormously for move
constructors. For ordinary functions it documents intent and lets the compiler skip some unwinding bookkeeping.

---

## 3. Overloading and overload resolution

Several functions can share a name if their parameter lists differ:

```cpp
void log(std::int64_t value);
void log(double value);
void log(std::string_view text);
```

For each call, the compiler finds every visible candidate with that name, discards those that can't accept the
arguments, and ranks the rest by how much conversion each argument needs. Simplified, from best to worst:

1. **Exact match** (including adding `const`, and array/function-to-pointer decay).
2. **Promotion**: small integers to `int` (`char`, `short`, `bool` → `int`), `float` → `double`.
3. **Standard conversion**: any other built-in conversion, such as `int` → `double`, `double` → `int`,
   `int64_t` → `int`, derived pointer → base pointer.
4. **User-defined conversion**: through a converting constructor or conversion operator.

If one candidate is at least as good for every argument and strictly better for one, it wins. Otherwise the call
is **ambiguous** and won't compile.

```cpp
log(42);          // int: no exact match. int -> int64_t and int -> double are both conversions: AMBIGUOUS
log(42L);         // long is int64_t on Linux: exact match
log(1.5f);        // float -> double is a promotion: picks log(double)
log("hello");     // const char[6] -> string_view: user-defined conversion: picks log(string_view)
```

The `log(42)` ambiguity surprises people. Integer literals are `int`, and `int` → `int64_t` is a *conversion*, not
a promotion, so it ties with `int` → `double`.

### Overloads that silently do the wrong thing

The more dangerous case is when resolution *succeeds* with something you didn't intend:

```cpp
void set_quantity(Order& o, std::int64_t qty);
set_quantity(order, 100.7);   // compiles: double -> int64_t truncates to 100
```

`-Wconversion` catches this one. A stronger fix is to make the wrong call impossible:

```cpp
void set_quantity(Order& o, std::int64_t qty);
void set_quantity(Order& o, double) = delete;   // calling with a double is now a compile error
```

**Deleted functions** take part in overload resolution and, if selected, produce an error. They're the standard
way to forbid specific conversions.

> [!TIP]
> Even better: give quantities and prices their own types, `struct Qty { std::int64_t v; };`, so a price can't be
> passed where a quantity is expected. This "strong typedef" pattern costs nothing at run time and catches a whole
> class of bugs at compile time.

### Exercises

:::exercise Which overload?
Given `void f(int); void f(double); void f(long);`, which is called by `f('a')`, `f(1.0f)`, `f(1u)`?
:::solution
`f('a')`: `char` → `int` is a promotion, which beats conversions to `double` or `long`, so `f(int)`.
`f(1.0f)`: `float` → `double` is a promotion, so `f(double)`.
`f(1u)`: `unsigned` → `int`, `long` and `double` are all *conversions* of equal rank, so it's **ambiguous**.
:::

---

## 4. Default arguments

```cpp
void send(const Order& o, bool post_only = false, int retries = 0);
send(o);                // send(o, false, 0)
send(o, true);          // send(o, true, 0)
```

Default arguments are filled in **at the call site** from the declaration the caller sees. Two consequences:

- Put them on the declaration in the header, not on the definition.
- Changing a default requires recompiling every caller; the old default is baked into their object code.

Boolean parameters with defaults also make call sites unreadable: `send(o, true, 3)` says nothing about what
`true` means. Prefer an `enum class` or a small options struct with designated initializers:

```cpp
struct SendOptions { bool post_only = false; int retries = 0; };
void send(const Order& o, SendOptions opts = {});

send(o, {.post_only = true});    // C++20 designated initializers: self-documenting
```

> [!WARNING]
> Default arguments on **virtual** functions are a trap: the default comes from the *static* type of the
> expression, while the function body comes from the dynamic type. Calling through a `Base*` uses `Base`'s
> default with `Derived`'s implementation. Don't put defaults on virtuals.

---

## 5. `inline`: a linkage keyword, not a speed keyword

The word `inline` means two unrelated things, which causes endless confusion.

**The `inline` keyword** is about the **One Definition Rule** (Chapter 1.1). It tells the linker: "this function
may be defined in several translation units; they're all identical; keep one." That's what lets you put a function
definition in a header that many `.cpp` files include.

```cpp quote.hpp
inline double mid(std::int64_t bid, std::int64_t ask) { return (bid + ask) / 2.0; }
```

Without `inline`, two `.cpp` files including this header would both define `mid`, and linking fails with "multiple
definition of `mid`". Functions defined inside a class body, `constexpr` functions, and templates are implicitly
inline in this sense.

**Inlining** is an optimization: replacing a call with the callee's body. The compiler decides this on its own,
using heuristics about size and call frequency. The `inline` keyword is at most a weak hint; modern GCC and Clang
mostly ignore it for that purpose.

The connection between the two is indirect but real: to inline a call, the compiler needs the body. Putting the
definition in a header (which requires the `inline` keyword) makes the body visible in every translation unit that
calls it.

```cpp
// pricing.hpp
double fee(double px);                 // declaration only: callers in other TUs can't inline it

// pricing.hpp (alternative)
inline double fee(double px) { return px * 0.0001; }   // body visible everywhere: inlinable
```

| Want | Do |
|---|---|
| A small hot function inlined across files | Define it in the header (it's then `inline` by necessity) |
| Inlining across `.cpp` files without moving code | Link-time optimization, `-flto` (Chapter 8.4) |
| To force inlining | `[[gnu::always_inline]]` (rarely needed; measure first) |
| To prevent inlining (e.g. cold error paths) | `[[gnu::noinline]]`, often with `[[gnu::cold]]` |

### Exercises

:::exercise Multiple definitions
`util.hpp` contains `int clamp_qty(int q) { return q < 0 ? 0 : q; }` (no `inline`), and it's included by `a.cpp`
and `b.cpp`. What happens when you build, and what are two fixes?
:::solution
The linker reports "multiple definition of `clamp_qty(int)`": each TU emits a strong definition. Fix it by adding
`inline` (or `constexpr`, which implies `inline`), or by keeping only a declaration in the header and moving the
definition to one `.cpp` file. The first fix keeps the body visible for inlining; the second doesn't.
:::

:::exercise The cold path
Why might you mark an error-reporting function `[[gnu::noinline, gnu::cold]]` in a hot loop?
:::solution
If the compiler inlines a large error-formatting function into a hot loop, the loop's code grows, using more
instruction cache and possibly pushing hot code apart. `noinline` keeps the body out of line, and `cold` tells the
compiler the call is unlikely, so it moves the call site out of the hot path and optimizes the branch layout
accordingly. The hot loop stays small and tight.
:::

---

## 6. Function pointers, briefly

A function's address can be stored and called through:

```cpp
double linear_fee(double px)  { return px * 0.0001; }
double tiered_fee(double px)  { return px < 1000 ? 1.0 : px * 0.00005; }

double (*fee)(double) = &linear_fee;   // or: using FeeFn = double(*)(double);
fee = tiered_fee;
double f = fee(1500.0);
```

A call through a function pointer is an **indirect call**. The CPU must predict the target, and the compiler can't
inline it unless it can prove which function it points to. Chapter 2.5 shows how lambdas and templates usually give
you the same flexibility with direct, inlinable calls, and when `std::function` is worth its cost.

---

## Summary

- On x86-64 Linux, up to six integer and eight floating-point arguments go in registers; small simple structs
  too. Types with non-trivial copy or destruction always go through memory.
- A call's direct cost is small. Its real cost is the optimizations it blocks when the body isn't visible.
- Overload resolution ranks exact match, promotion, conversion, then user-defined conversion. `int` → `int64_t`
  is a conversion, not a promotion.
- Use `= delete` to forbid dangerous overloads, and strong types to prevent mixing up quantities and prices.
- Defaults are filled in at the call site; prefer option structs over bare booleans; never default virtuals.
- `inline` the keyword is about the ODR. Inlining the optimization is the compiler's decision, enabled by making
  bodies visible.

## Key terms

**Calling convention** · **caller-saved / callee-saved** · **signature** · **overload resolution** ·
**promotion** · **deleted function** · **default argument** · **`[[nodiscard]]`** · **`inline`** ·
**indirect call**
