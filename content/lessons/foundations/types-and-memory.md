+++
title = "Types, Objects and Memory"
minutes = 40
practice_tags = ["memory", "alignment", "numerics", "bit-manipulation"]
+++

## Why this chapter matters

Three things that happen in real quant code:

1. A risk report sums the day's traded notional in an `int`. On a busy day the total passes 2,147,483,647 and the
   report shows a large *negative* number. Worse, signed overflow is undefined behaviour, so the optimizer was
   entitled to do anything at all.
2. A loop over 50 million `Order` structs takes 120 ms. Reordering three fields in the struct, with no algorithm
   change, makes it 90 ms, because each `Order` shrank from 32 bytes to 24 and more of them fit in each cache line.
3. `if (price == 0.3)` never fires, even though the price "is" 0.3, because 0.1 + 0.2 is not 0.3 in binary
   floating point.

C++ gives you exact control over how data is represented in memory. That's where its performance comes from, but
it also means *you* are responsible for knowing how big a type is, what range it covers, how it's aligned, and
how long an object lives. This chapter builds that mental model.

By the end of this chapter you should be able to:

- choose integer types deliberately and avoid overflow and signed/unsigned traps;
- explain why exchanges and trading systems represent prices as integers;
- describe the four storage durations and the difference between stack and heap;
- predict `sizeof` and `alignof` for a struct and shrink it by reordering fields;
- recognize the most common forms of undefined behaviour.

---

## 1. Integer types: sizes, ranges, and overflow

The standard only guarantees *minimum* sizes for `int`, `long` and friends. On mainstream 64-bit Linux (the LP64
data model) they are:

| Type | Size (bytes) | Range |
|---|---|---|
| `char` / `signed char` / `unsigned char` | 1 | -128..127 / 0..255 |
| `short` | 2 | ±32,767 |
| `int` | 4 | ±2.1 × 10⁹ |
| `long` | 8 on Linux, **4 on Windows** | ±9.2 × 10¹⁸ |
| `long long` | 8 | ±9.2 × 10¹⁸ |
| `size_t` | 8 | 0..1.8 × 10¹⁹ |

Because `long` differs between platforms, use the fixed-width types from `<cstdint>` whenever the size matters:
`int32_t`, `int64_t`, `uint8_t`, `uint64_t`, and so on.

### Signed overflow is undefined; unsigned wraps

```cpp
int32_t a = INT32_MAX;
a + 1;            // undefined behaviour: the compiler may assume this never happens

uint32_t b = 0;
b - 1;            // well defined: wraps to 4294967295
```

Because signed overflow "can't happen", the optimizer is allowed to simplify `x + 1 > x` to `true` and to widen
loop counters. That's good for speed, and catastrophic if your code actually overflows.

### Mixing signed and unsigned

When you mix `int` and `unsigned` in an expression, the `int` is converted to unsigned:

```cpp
std::vector<int> v;               // empty
for (int i = 0; i < v.size() - 1; ++i) { /* ... */ }
// v.size() - 1 == SIZE_MAX (wrapped), so the loop runs ~forever and reads garbage
```

Rules of thumb: use signed types (`int64_t`, or `std::ptrdiff_t`) for quantities and arithmetic, use `size_t` for
sizes and indices as the standard library does, and compile with `-Wsign-conversion`. C++20 adds `std::ssize(v)`,
a signed size.

> [!QUANT]
> Quantities and notionals belong in `int64_t`. Real numbers overflow 32 bits: 10,000 shares at $250 is
> $2.5 million, or 250 million cents. A day of notional across a book easily passes 2³¹ cents (about $21 million).

### Exercises

:::exercise Overflow arithmetic
What does `int32_t x = 50'000 * 50'000;` produce? Fix it.
:::solution
`50'000 * 50'000` is evaluated in `int` and is 2.5 × 10⁹, which is more than `INT32_MAX` (≈ 2.147 × 10⁹). That's
signed overflow, which is undefined behaviour. In practice you usually see a wrapped negative number, and GCC
warns `integer overflow in expression`. Fix it by doing the arithmetic in 64 bits and storing the result in a
64-bit variable: `int64_t x = int64_t{50'000} * 50'000;`.
:::

:::exercise Size of long
Your colleague writes `long notional_cents` and the code is also built for a Windows research environment.
What could go wrong?
:::solution
On Windows (the LLP64 model) `long` is 32 bits, so notionals above ≈ $21.4M in cents overflow there and not on
Linux. You get platform-dependent bugs that are very hard to reproduce. Use `int64_t`.
:::

---

## 2. Floating point, briefly, and why prices are integers

`float` (32-bit) and `double` (64-bit) are IEEE 754 binary floating point. They store a sign, an exponent and a
binary fraction, so most decimal fractions **can't be represented exactly**:

```cpp
#include <cstdio>
int main() {
    double x = 0.1 + 0.2;
    std::printf("%.17f\n", x);          // 0.30000000000000004
    std::printf("%d\n", x == 0.3);      // 0
}
```

`double` has about 15–16 significant decimal digits. That's plenty for *analytics* such as volatilities, model
prices and returns. But **exact** quantities like order prices and cash need to be compared, hashed and summed
exactly, and there floating point causes subtle bugs: prices that don't match a book level, totals that drift by
a cent.

The industry solution is **fixed-point**: store prices as an integer number of *ticks* (the minimum price
increment) or of some fixed fraction:

```cpp
using Price = int64_t;                       // price in units of 1e-4 dollars
constexpr Price kScale = 10'000;

constexpr Price to_price(double px)  { return static_cast<Price>(px * kScale + (px >= 0 ? 0.5 : -0.5)); }
constexpr double to_double(Price p)  { return static_cast<double>(p) / kScale; }

static_assert(to_price(101.25) == 1'012'500);
```

Integer prices compare exactly, make good hash keys and array indices (very useful for order books: see the [Limit Order Book project](/projects/order-book.html)),
and integer arithmetic is fast and deterministic. Chapter 14.1 covers floating point in depth.

### Exercises

:::exercise Converting prices
Why does `to_price` add 0.5 (or subtract it for negative prices) before the cast? What does
`static_cast<int64_t>(0.29 * 100)` give, for a price of $0.29 in cents?
:::solution
`static_cast` to an integer **truncates toward zero**. `0.29` isn't exactly representable, and `0.29 * 100`
evaluates to `28.999999999999996`, which truncates to **28**, one cent off. Adding 0.5 first rounds to the
nearest integer and gives `29`. (`std::llround` does the same thing more explicitly.)
:::

:::exercise Equal prices
You need to check whether a trade price equals the best bid, both stored as `double`. Give two options better
than `==` and say which you'd use in an order book.
:::solution
(1) Compare with a tolerance smaller than half a tick: `std::fabs(a - b) < tick / 2`. (2) Better: convert both to
integer ticks once, at the boundary where data enters the system, and compare integers exactly from then on. Order
books should use option 2. It's exact, faster, and the integer price can index arrays.
:::

---

## 3. Objects and storage duration

In C++ an **object** is a region of storage with a type and a **lifetime**. Every object has one of four storage
durations:

| Storage duration | How you get it | Lifetime | Where it usually lives |
|---|---|---|---|
| Automatic | local variables | until the enclosing block ends | the stack (or registers) |
| Static | globals, `static` locals, `static` members | the whole program | the data/bss segment |
| Thread | `thread_local` | the thread's lifetime | per-thread storage |
| Dynamic | `new`, `std::make_unique`, containers | until explicitly freed | the heap |

**The stack** is a contiguous region per thread. Allocating on it is a single subtraction from the stack pointer,
it's almost always hot in cache, and it frees itself automatically. **The heap** is managed by an allocator
(`malloc` underneath `new`). Each allocation and deallocation takes tens to hundreds of nanoseconds and may take a
lock or even make a system call. Heap objects can also end up scattered across memory, which costs cache misses.

```svg "The vector and unique_ptr objects live on the stack, but they point to memory on the heap."
<svg viewBox="0 0 660 262" xmlns="http://www.w3.org/2000/svg" role="img"><defs><marker id="arrowhead" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path class="head" d="M0,0 L10,5 L0,10 z"/></marker></defs><text class="title" x="40" y="24">Stack: on_quote's frame</text><text class="title" x="400" y="24">Heap</text><rect class="box" x="40" y="38" width="240" height="34" rx="8"/><text class="mono" x="160.0" y="59.0" text-anchor="middle">bid</text><rect class="box" x="40" y="78" width="240" height="34" rx="8"/><text class="mono" x="160.0" y="99.0" text-anchor="middle">ask</text><rect class="box" x="40" y="118" width="240" height="34" rx="8"/><text class="mono" x="160.0" y="139.0" text-anchor="middle">mid</text><rect class="box-strong" x="40" y="158" width="240" height="34" rx="8"/><text class="mono" x="160.0" y="179.0" text-anchor="middle">levels  { ptr | size | cap }</text><rect class="box-strong" x="40" y="198" width="240" height="34" rx="8"/><text class="mono" x="160.0" y="219.0" text-anchor="middle">snapshot  { ptr }</text><rect class="box" x="400" y="60" width="220" height="56" rx="8"/><text class="mono" x="510.0" y="92.0" text-anchor="middle">10 × double  (80 B)</text><rect class="box" x="400" y="172" width="220" height="50" rx="8"/><text class="mono" x="510.0" y="201.0" text-anchor="middle">Book</text><path class="arrow" d="M282,175 C340,175 340,88 398,88"/><path class="arrow" d="M282,215 C340,215 340,197 398,197"/><text class="muted" x="160" y="250" text-anchor="middle">freed automatically on return</text><text class="muted" x="510" y="250" text-anchor="middle">a malloc/free per allocation</text></svg>
```


```cpp
void on_quote(double bid, double ask) {
    double mid = 0.5 * (bid + ask);              // automatic: essentially free
    std::vector<double> levels(10);              // the vector object is automatic,
                                                 // but its 10 doubles are heap-allocated!
    auto snapshot = std::make_unique<Book>();    // dynamic: a heap allocation
}
```

> [!PERF]
> The hot path of a low-latency system typically does **zero heap allocations**. Memory is allocated up front at
> startup (pre-sized vectors, object pools, arenas), and the hot path only reuses it. Chapter 7.4 is devoted to
> this.

### Exercises

:::exercise Count the allocations
How many heap allocations does this function make per call, with libstdc++ (whose `std::string` stores up to 15
characters inline) and a non-empty `fills`?

```cpp
std::string describe(const std::vector<int>& fills) {
    std::vector<int> copy = fills;
    std::string s = "fills: " + std::to_string(copy.size());
    return s;
}
```
:::solution
**One.** `copy` allocates a buffer for its ints. `std::to_string(copy.size())` produces a few digits, and
`"fills: " + ...` produces something like `"fills: 42"`. Both fit in the 15-character inline buffer, so neither
allocates. `return s;` doesn't allocate either, because the copy is elided (or at worst it's a move). If the
strings were longer than 15 characters, you'd get up to two more allocations. Counting allocations like this, and
confirming with a profiler or a hooked `operator new`, is a core low-latency skill. (Better still: take
`std::span<const int>` and don't copy at all.)
:::

:::exercise Dangling
What's wrong with this, and which tool would catch it?

```cpp
const double* best_level() {
    double levels[3] = {100.0, 99.99, 99.98};
    return &levels[0];
}
```
:::solution
`levels` has automatic storage duration and dies when the function returns, so the returned pointer **dangles**.
Reading through it is undefined behaviour: it may *appear* to work until the stack slot is reused. GCC warns
(`-Wreturn-local-addr`), and ASan with stack-use-after-return detection catches it at run time.
:::

---

## 4. Alignment, padding, and `sizeof`

Every type has an **alignment** requirement: its address must be a multiple of `alignof(T)`. On x86-64, `int`
needs 4-byte alignment, `double` and pointers need 8, and `char` needs 1. To satisfy alignment the compiler
inserts **padding** between struct members, and at the end so that arrays of the struct stay aligned.

```cpp
struct OrderBad {
    char    side;     // 1 byte  + 7 padding
    int64_t price;    // 8 bytes
    int32_t qty;      // 4 bytes
    bool    active;   // 1 byte  + 3 padding (to a multiple of 8)
};                    // sizeof == 24, of which 10 bytes are padding

struct OrderGood {
    int64_t price;    // 8
    int32_t qty;      // 4
    char    side;     // 1
    bool    active;   // 1 + 2 padding
};                    // sizeof == 16

static_assert(sizeof(OrderBad) == 24 && sizeof(OrderGood) == 16);
```

```layout "The same four fields. Ordering them largest-first removes 8 bytes of padding."
title: OrderBad: sizeof 24
side:1:3 pad:7 price:8:1 qty:4:2 active:1:4 pad:3

title: OrderGood: sizeof 16
price:8:1 qty:4:2 side:1:3 active:1:4 pad:2
```

Viewed per cache line, the difference becomes obvious:

```layout "A 64-byte cache line holds 4 OrderGood objects but only 2⅔ OrderBad objects, so the third straddles two lines."
title: OrderGood: 4 per line
row: 64
order_0:16:2 order_1:16:3 order_2:16:4 order_3:16:5

title: OrderBad: 2.67 per line
row: 64
order_0:24:2 order_1:24:3 order_2…:16:4
```


The rule: **order members from largest alignment to smallest** to minimize padding. Same data, a third less
memory, so 4 orders fit in a 64-byte cache line instead of 2.67. For an array of millions of orders that's a
direct speedup on every scan.

You can raise alignment with `alignas`, which we'll use to give shared counters their own cache line in the
concurrency chapters:

```cpp
struct alignas(64) PaddedCounter { std::atomic<int64_t> value; };   // sizeof == 64
```

### Exercises

:::exercise Predict the size
What are `sizeof` and `alignof` of `struct Q { bool a; double b; bool c; };`? Reorder it to minimize size.
:::solution
`alignof(Q) == 8` (from `double`). Layout: `a` at 0, 7 bytes of padding, `b` at 8, `c` at 16, then 7 bytes of tail
padding, so `sizeof(Q) == 24`. Reordered as `{ double b; bool a; bool c; }`, it's 8 + 1 + 1 + 6 padding = **16**.

```layout
title: struct Q { bool a; double b; bool c; }: 24 bytes
a:1:3 pad:7 b:8:1 c:1:4 pad:7

title: reordered { double b; bool a; bool c; }: 16 bytes
b:8:1 a:1:3 c:1:4 pad:6
```

:::

:::exercise Why tail padding?
Why does the compiler add padding at the *end* of a struct, where no member follows?
:::solution
For arrays. In `Q arr[2]`, `arr[1]` starts exactly `sizeof(Q)` bytes after `arr[0]`. If `sizeof` weren't a
multiple of the alignment, `arr[1].b` would be misaligned. So `sizeof(T)` is always a multiple of `alignof(T)`.
:::

---

## 5. Undefined behaviour

**Undefined behaviour (UB)** is any operation the standard places *no requirements* on. The compiler may assume UB
never happens and optimize on that basis, so the consequences can show up far from the bug, change with the
optimization level, and change between compiler versions. The most common kinds:

- reading an **uninitialized** variable;
- **out-of-bounds** array access, including one past the end via `operator[]`;
- **signed integer overflow**, and shifting by ≥ the bit width;
- dereferencing a **null or dangling** pointer (use-after-free, returning a reference to a local);
- **data races**: two threads accessing the same memory, at least one writing, without synchronization;
- violating **strict aliasing**, i.e. accessing an object through a pointer to an unrelated type.

The aliasing rule matters for parsing binary market data, where it's tempting to write:

```cpp
const char* buf = /* bytes from the network */ nullptr;
uint32_t seq = *reinterpret_cast<const uint32_t*>(buf + 4);   // UB: aliasing + possible misalignment
```

The correct, and equally fast, approach is `std::memcpy`. Compilers turn a fixed-size memcpy into a single load:

```cpp
#include <cstring>
#include <cstdint>

inline uint32_t load_u32(const char* p) {
    uint32_t v;
    std::memcpy(&v, p, sizeof v);     // well-defined; compiles to one mov instruction
    return v;
}
```

For reinterpreting the bits of a value, for example a `double`'s bits as a `uint64_t`, C++20 provides
`std::bit_cast<uint64_t>(x)`.

> [!WARNING]
> "It works on my machine" is not evidence that code is correct in the presence of UB. Build with
> `-fsanitize=address,undefined` and `-Wall -Wextra`, and treat every report as a bug.

### Exercises

:::exercise UB or not?
For each one, say whether it's UB: (a) `uint8_t x = 255; ++x;` (b) `int a[4]; int* p = a + 4;`
(c) `int a[4]; int v = a[4];` (d) `int i; if (i > 0) {}` (e) `int64_t big = 1 << 40;`
:::solution
(a) Not UB. `++x` promotes to `int`, computes 256, then converts back to `uint8_t`, giving 0. Unsigned conversion
is well defined. (b) Not UB: forming a pointer one past the end is allowed. (c) UB: reading one past the end.
(d) UB: reading an uninitialized variable. (e) UB: `1` is an `int`, and shifting a 32-bit `int` by 40 overflows.
Write `int64_t{1} << 40`.
:::

:::exercise The vanishing check
Why might the optimizer delete the `if` below entirely?

```cpp
bool will_overflow(int x) { return x + 1 < x; }
```
:::solution
Signed overflow is UB, so the compiler may assume `x + 1` never overflows. Under that assumption `x + 1 < x` is
always false, and GCC and Clang at `-O2` compile the function to `return false;`. The right way to check is to
compare *before* doing the arithmetic, `x == INT_MAX`, or to use `__builtin_add_overflow` (or C++26's
`std::add_sat` family).
:::

---

## Summary

- Use `<cstdint>` fixed-width types. Quantities and notionals go in `int64_t`.
- Signed overflow is UB; unsigned arithmetic wraps. Watch out when mixing them.
- `double` is fine for analytics, but represent **prices as integer ticks**.
- Stack allocation is essentially free; heap allocation costs tens to hundreds of ns, so keep it off the hot path.
- Structs are padded for alignment. **Order fields from largest to smallest** and check with `static_assert(sizeof...)`.
- UB is a contract with the optimizer. Use `memcpy`/`bit_cast` for type punning, and run sanitizers.

## Key terms

**Fixed-width integer** · **two's complement** · **fixed-point** · **storage duration** · **stack** · **heap** ·
**alignment** · **padding** · **undefined behaviour** · **strict aliasing**
