+++
title = "Vocabulary Types"
minutes = 45
practice_tags = ["variant", "stl", "strings"]
+++

## Why this chapter matters

Look at these four function signatures from an older codebase:

```cpp
double lookup_price(const char* symbol);            // returns -1.0 if not found... or is -1.0 a valid spread?
int    parse_msg(const char* buf, int len, Msg* out); // returns 0 on success, or an error code, or a length?
void   apply(int msg_type, void* msg);              // what does msg point to?
void   sum(const double* prices, int n);            // n elements or n bytes? can prices be null?
```

Each one encodes its real meaning in comments, conventions or magic values that the compiler can't check. Modern
C++ has a set of small, standard **vocabulary types** that say these things in the type system instead:

```cpp
std::optional<double>          lookup_price(std::string_view symbol);
std::expected<Msg, ParseError> parse_msg(std::span<const std::byte> buf);
void                           apply(const std::variant<AddOrder, Cancel, Execute>& msg);
double                         sum(std::span<const double> prices);
```

They're called "vocabulary" types because they're meant to be shared across every library and interface, the way
`int` and `std::string` are. Almost all are zero-overhead or close to it. This chapter covers each one, what it
costs, and the traps.

By the end of this chapter you should be able to:

- use `std::optional`, `std::variant`, `std::span` and `std::string_view` in interfaces, and know their layout;
- dispatch on a variant with `std::visit` and the overloaded-lambdas idiom;
- use structured bindings and `enum class`;
- avoid the dangling-view bugs that non-owning types make possible.

(`std::expected` gets the full treatment in Chapter 2.7.)

---

## 1. `enum class`: named constants with a type

```cpp
enum Side { Buy, Sell };          // old style: Buy and Sell leak into the enclosing scope, convert to int
enum class Side : std::uint8_t {  // scoped enumeration
    Buy,
    Sell,
};
```

`enum class` fixes two problems with plain enums. Its enumerators are scoped (`Side::Buy`, so they can't collide
with another `Buy`), and they don't implicitly convert to `int`, so you can't accidentally pass a `Side` where a
quantity is expected. The `: std::uint8_t` fixes the **underlying type**, which controls its size and layout in
structs and on the wire.

When you do need the number, ask for it explicitly with `std::to_underlying(side)` (C++23) or
`static_cast<std::uint8_t>(side)`.

> [!TIP]
> In a `switch` over an `enum class`, don't write a `default:` case. Then `-Wswitch` (in `-Wall`) warns if you
> add an enumerator and forget to handle it somewhere. That warning catches real bugs when someone adds
> `OrderType::StopLimit` to an existing system.

---

## 2. Structured bindings

A **structured binding** declares several names at once, bound to the elements of a struct, pair, tuple or array:

```cpp
std::map<std::int64_t, std::int64_t> levels;   // price -> qty

for (const auto& [price, qty] : levels)        // instead of it->first, it->second
    std::printf("%lld x %lld\n", (long long)price, (long long)qty);

auto [it, inserted] = levels.try_emplace(10050, 0);

struct Fill { std::int64_t price, qty; };
auto [px, q] = last_fill();
```

The names are aliases into a single hidden object, so `const auto& [a, b]` binds the hidden object by const
reference and copies nothing. It's purely a readability feature with no run-time cost.

---

## 3. `std::optional<T>`: maybe a value

`std::optional<T>` holds either a `T` or nothing. It replaces magic sentinel values (`-1`, `0`, `NaN`,
`nullptr`) and the "bool success plus output parameter" pattern.

```cpp
std::optional<std::int64_t> best_bid(const Book& b) {
    if (b.bids.empty()) return std::nullopt;
    return b.bids.front().price;
}

if (auto bid = best_bid(book)) {          // contextually converts to bool
    quote(*bid - 1);                      // *bid: the value (UB if empty!)
}
std::int64_t px = best_bid(book).value_or(0);
```

**Layout.** An `optional<T>` stores the `T` **inline**, plus a `bool`. No heap allocation ever happens. Its size is
`sizeof(T)` plus the bool rounded up for alignment: `sizeof(std::optional<std::int64_t>)` is 16,
`sizeof(std::optional<std::int32_t>)` is 8.

```layout "std::optional<int64_t>: the value inline plus a flag, padded to the 8-byte alignment of int64_t."
title: std::optional<std::int64_t>
row: 16
value:8:1 engaged:1:3 pad:7
```

| Access | Behaviour when empty |
|---|---|
| `*opt`, `opt->member` | undefined behaviour (no check) |
| `opt.value()` | throws `std::bad_optional_access` |
| `opt.value_or(x)` | returns `x` |
| `opt.has_value()`, `if (opt)` | false |

C++23 adds monadic operations, which chain operations that might each produce nothing:

```cpp
std::optional<double> mid = best_bid(book).and_then([&](std::int64_t bid) {
    return best_ask(book).transform([=](std::int64_t ask) { return (bid + ask) / 2.0; });
});
```

> [!NOTE]
> `std::optional<T&>` isn't allowed before C++26. To return "maybe a reference to an existing object", return a
> `T*` (null meaning "none"). That's exactly the non-owning-optional role that raw pointers keep in modern C++.

### Exercises

:::exercise Sentinels
`double implied_vol(const Option& o, double price)` returns `-1.0` when the solver fails to converge. List two ways
this convention can produce wrong results, and rewrite the signature.
:::solution
(1) A caller forgets to check and feeds `-1.0` into a risk calculation as if it were a volatility, producing
nonsense Greeks without any error. (2) A caller checks `vol < 0` but another part of the code uses `NaN` or `0.0`
for "no value", so the conventions mismatch. Rewrite: `std::optional<double> implied_vol(const Option&, double)`,
or `std::expected<double, SolverError>` if callers need to know *why* it failed.
:::

---

## 4. `std::variant<Ts...>`: one of several types

A `std::variant<A, B, C>` holds exactly one value, of type `A`, `B` or `C`, and knows which one. It's a type-safe
tagged union, and it's the natural representation for a message from a feed:

```cpp
struct AddOrder { std::uint64_t id; Side side; std::int64_t price, qty; };
struct Cancel   { std::uint64_t id; std::int64_t qty; };
struct Execute  { std::uint64_t id; std::int64_t qty; std::uint64_t match_id; };

using Message = std::variant<AddOrder, Cancel, Execute>;
```

**Layout.** Like `optional`, it's inline: the size is the largest alternative plus a small index, rounded up for
alignment. Here, `AddOrder` is 32 bytes (with padding after `side`), so `sizeof(Message)` is 40. No heap, ever.

```layout "std::variant<AddOrder, Cancel, Execute>: storage big enough for the largest alternative, plus an index saying which one is live."
title: Message holding an AddOrder
row: 40
id:8:1 side:1:1 pad:7 price:8:1 qty:8:1 index_=_0:1:3 pad:7
```

### Visiting

To act on a variant, use `std::visit` with a callable that accepts every alternative. The cleanest form is the
**overloaded lambdas** idiom:

```cpp
template <class... Fs> struct overloaded : Fs... { using Fs::operator()...; };

void apply(Book& book, const Message& msg) {
    std::visit(overloaded{
        [&](const AddOrder& m) { book.add(m.id, m.side, m.price, m.qty); },
        [&](const Cancel& m)   { book.reduce(m.id, m.qty); },
        [&](const Execute& m)  { book.execute(m.id, m.qty); },
    }, msg);
}
```

`overloaded` inherits from every lambda and pulls in all their call operators, giving one object with an overload
for each message type. If you forget a case, the call doesn't compile. That's the big advantage over a `switch` on
an integer type field.

You can also test and extract directly:

```cpp
if (const auto* add = std::get_if<AddOrder>(&msg)) { /* use add->price */ }
msg.index();                     // 0, 1 or 2
std::holds_alternative<Cancel>(msg);
std::get<Cancel>(msg);           // throws std::bad_variant_access if it isn't a Cancel
```

> [!PERF]
> `std::visit` on a single variant compiles to a jump table or a chain of comparisons on the index, then a direct
> (inlinable) call: no virtual dispatch, no heap. For a handful of alternatives it typically beats a virtual call
> on a heap-allocated message object, mainly because the messages are stored by value, contiguously. Chapter 12.5
> compares the two designs with measurements.

### Variant vs inheritance

| | `std::variant` | Virtual base class |
|---|---|---|
| Set of types | closed: fixed at compile time | open: anyone can add a derived class |
| Storage | inline, by value | usually heap, through a pointer |
| Adding an operation | easy: write a new visitor | hard: add a virtual to every class |
| Adding a type | hard: every visitor must handle it (the compiler tells you where) | easy |
| Missing-case check | compile error | none (or a pure virtual) |

Messages in a protocol are a closed set, so variant fits. Strategy plug-ins loaded from configuration are an open
set, so virtuals fit.

### Exercises

:::exercise Size of a variant
What is `sizeof(std::variant<std::int32_t, double, char>)` on x86-64, and why?
:::solution
16. The largest alternative is `double` (8 bytes, alignment 8). The index needs at least one byte, and the whole
object must be a multiple of the 8-byte alignment, so 8 + 1 rounds up to 16.
:::

:::exercise Missing case
You add `struct Replace { ... };` to `Message`. What happens to the `apply` function above?
:::solution
It stops compiling: the `overloaded` set has no call operator accepting `const Replace&`, so `std::visit` can't
call it for that alternative. The compiler shows you every visitor that needs updating. (Unless one of the lambdas
is a catch-all `[](const auto&) {}`, which silently swallows the new type, so use catch-alls sparingly.)
:::

---

## 5. `std::string_view`: a window onto characters

`std::string_view` is a non-owning reference to a sequence of characters: a pointer and a length, 16 bytes,
passed in two registers.

```cpp
bool is_option_symbol(std::string_view sym) {           // accepts std::string, char*, literals, substrings
    return sym.size() > 6 && sym.substr(sym.size() - 2) == ".O";   // substr on a view: no allocation
}
```

Compare with `const std::string&`: passing a string literal or a `char*` to that creates a temporary
`std::string`, which may allocate. `string_view` accepts all of them for free, and `substr` just adjusts the
pointer and length. It's the right parameter type for read-only string inputs. Chapter 4.5 builds a zero-
allocation parser on it.

The catch is that it **doesn't own** anything:

```cpp
std::string_view sym = get_symbol_string();   // returns std::string by value: temporary destroyed at ';'
use(sym);                                     // dangling: reads freed memory

std::string_view a = std::string("AAPL") + ".O";   // same bug
```

Rules that keep you safe:

- Use `string_view` freely as a **function parameter**.
- Be very careful **returning** it or storing it as a **member**: only when it views storage that's guaranteed to
  outlive it (a string literal, a buffer the caller owns, a long-lived intern table).
- A `string_view` isn't necessarily null-terminated. Don't pass `sv.data()` to a C function expecting a
  C string.

---

## 6. `std::span<T>`: a window onto contiguous elements

`std::span<T>` is the same idea for arrays: a pointer and a length, viewing any contiguous sequence of `T`.

```cpp
double vwap(std::span<const Trade> trades) {
    double notional = 0, volume = 0;
    for (const Trade& t : trades) { notional += t.price * t.qty; volume += t.qty; }
    return volume ? notional / volume : 0.0;
}

std::vector<Trade> tape = load();
std::array<Trade, 8> recent = last_eight();
Trade raw[100];

vwap(tape);                         // all accepted, no copies
vwap(recent);
vwap(raw);
vwap(std::span(tape).last(50));     // last 50 trades: just pointer arithmetic
```

It replaces the `(const T* data, size_t n)` pair, which can be passed in the wrong order or with the wrong count,
and it replaces taking `const std::vector<T>&`, which forces callers to have a vector.

Two forms:

- `std::span<T>` (dynamic extent): pointer plus size, 16 bytes.
- `std::span<T, N>` (static extent): just a pointer; the size is part of the type. Useful for fixed-size fields
  like an 8-byte symbol in a binary message.

`span<const T>` gives read-only access; `span<T>` lets the function modify elements (but never resize). Like
`string_view`, it's non-owning, and it's invalidated by anything that reallocates the underlying vector.

### Exercises

:::exercise Replace the signature
Rewrite each signature using vocabulary types:
(a) `bool find_level(int64_t price, Level* out);`
(b) `void fill_prices(double* out, size_t n);`
(c) `int classify(int type, void* payload);`
(d) `bool symbol_matches(const std::string& s);` (called mostly with string literals)
:::solution
(a) `std::optional<Level> find_level(std::int64_t price);` (or `const Level*` if the caller needs a reference to
the level inside the book).
(b) `void fill_prices(std::span<double> out);`
(c) `int classify(const std::variant<A, B, C>& payload);` with the concrete payload types.
(d) `bool symbol_matches(std::string_view s);`, avoiding a temporary `std::string` per call.
:::

:::exercise Dangling span
Why is this a bug, even though it usually appears to work?

```cpp
std::vector<double> prices = initial();
std::span<const double> view = prices;
prices.push_back(1.0);
double first = view[0];
```
:::solution
`push_back` may reallocate the vector's buffer if `size() == capacity()`. Then `view` still points to the old,
freed buffer, and `view[0]` is a heap-use-after-free. It "usually works" because freed memory often still holds the
old values, and because the vector may have had spare capacity. ASan reports it reliably when reallocation happens.
:::

---

## Summary

- `enum class` gives scoped, strongly typed constants with a fixed underlying type.
- Structured bindings name the parts of a struct, pair or tuple at no cost.
- `std::optional<T>` replaces sentinels; it stores the value inline with a flag. `*opt` doesn't check.
- `std::variant` is a type-safe tagged union, stored inline. Visit it with overloaded lambdas; missing cases fail
  to compile. Use it for closed sets of types like protocol messages.
- `std::string_view` and `std::span` are non-owning pointer-plus-length views. Use them as parameters; be very
  careful storing or returning them.
- All of these are free or nearly free: none allocates.

## Key terms

**Vocabulary type** · **`enum class`** · **underlying type** · **structured binding** · **`std::optional`** ·
**`std::variant`** · **`std::visit`** · **overloaded lambdas** · **closed vs open set** · **`std::string_view`** ·
**`std::span`** · **static extent** · **non-owning view**
