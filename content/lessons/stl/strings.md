+++
title = "Strings and string_view"
minutes = 40
practice_tags = ["strings", "parsing", "stl"]
+++

## Why this chapter matters

A FIX message is a string of `tag=value` fields separated by the SOH byte (`0x01`):

```text
8=FIX.4.4|9=148|35=D|49=CLIENT|56=BROKER|11=ORD42|55=AAPL|54=1|38=100|44=187.25|40=2|...
```

The first version of a parser splits it into a `std::vector<std::string>`, then splits each field on `=` into two
more `std::string`s, then calls `std::stod` on the price. That's dozens of heap allocations per message, and the
parser runs at about 200,000 messages per second. A version that works entirely with `std::string_view` and
`std::from_chars`, allocating nothing, runs at several million.

Text is everywhere in trading infrastructure: FIX, configuration files, CSV market data, symbols, logs. C++ strings
are powerful but easy to use expensively. This chapter explains exactly when they allocate, and how to parse text
without copying it.

By the end of this chapter you should be able to:

- describe the layout of `std::string`, including small-string optimization;
- predict which string operations allocate, and avoid unnecessary copies;
- write zero-allocation parsers with `std::string_view`;
- convert between text and numbers with `std::from_chars` and `std::to_chars`.

---

## 1. Inside `std::string`

A `std::string` in libstdc++ is 32 bytes: a pointer to the characters, a size, and a 16-byte union that holds either
the heap capacity or the characters themselves.

```layout "libstdc++ std::string. Short strings (up to 15 chars plus the terminating null) are stored inside the object, in the union, and data points at it. Longer strings are on the heap, and the union holds the capacity."
title: short string "AAPL" (SSO: no heap allocation)
row: 32
data_ptr:8:1 size_=_4:8:2 A_A_P_L_\0_...:16:3

title: long string (heap-allocated)
row: 32
data_ptr_→_heap:8:1 size:8:2 capacity:8:4 unused:8:6
```

This is **small-string optimization (SSO)**: strings up to 15 characters (libstdc++, MSVC) or 22 characters
(libc++) live inside the object, with no heap allocation. Ticker symbols, venue codes, order IDs and most FIX tag
values fit. Creating, copying and destroying a short string is just copying 32 bytes.

Longer strings live on the heap. Constructing one allocates; copying one allocates and copies; destroying one
frees.

> [!NOTE]
> SSO makes moving short strings no cheaper than copying them: the characters are inside the object, so a move
> must copy them too. It's still cheap (32 bytes), just not a pointer swap. It also means a moved-from short string
> isn't guaranteed to be empty.

### What allocates

For strings longer than the SSO limit:

| Operation | Allocates? |
|---|---|
| `std::string s = "a long literal ...";` | yes |
| `std::string t = s;` (copy) | yes |
| `std::string t = std::move(s);` | no (steals the buffer) |
| `s + t` | yes: a new string for the result (and another for each `+` in a chain) |
| `s += t` | only if capacity is exceeded (geometric growth, like `vector`) |
| `s.substr(i, n)` | yes: `substr` returns a new `std::string` |
| `s.reserve(n)` | once |
| passing a `const char*` to a `const std::string&` parameter | yes: a temporary `std::string` is built |
| `std::to_string(x)` | usually not (numbers fit in SSO) |
| `s.c_str()`, `s.data()`, `s.size()`, `s[i]` | no |

The `const char*` row is the sneakiest: a function that takes `const std::string&` and is called with string
literals or `char*` buffers constructs (and possibly allocates) a temporary on every call. Section 2 fixes it.

### Exercises

:::exercise How many allocations?
How many heap allocations does this function perform, assuming every string involved is longer than 15 characters?

```cpp
std::string make_key(const std::string& venue, const std::string& symbol) {
    return venue + ":" + symbol + ":" + "EQUITY";
}
```
:::solution
`venue + ":"` allocates a new string (1). Each subsequent `+` takes the left operand as an rvalue: libstdc++'s
`operator+(std::string&&, ...)` appends to the existing temporary, allocating only when its capacity is exceeded,
which may happen once or twice as it grows. So roughly **1–3** allocations. Pre-sizing avoids the extra ones:

```cpp
std::string key;
key.reserve(venue.size() + symbol.size() + 8);
key.append(venue).append(":").append(symbol).append(":EQUITY");
```
That's exactly one allocation, and with C++20, `std::format` or a reused buffer can do similarly.
:::

---

## 2. `std::string_view`: borrowing characters

As introduced in Chapter 2.6, `std::string_view` is a pointer and a length referring to characters owned by someone
else. It never allocates, and it can view any contiguous characters: a `std::string`, a literal, a `char` buffer
from the network, or a piece of any of those.

```cpp
bool is_order_msg(std::string_view msg) {
    return msg.find("\x01" "35=D\x01") != std::string_view::npos;
}

is_order_msg(std::string("..."));   // views the string
is_order_msg("8=FIX.4.4\x01...");   // views the literal: no temporary std::string
is_order_msg({buf, len});           // views a raw receive buffer
```

`substr`, `remove_prefix` and `remove_suffix` on a view just adjust the pointer and length: O(1), no allocation.
That's what makes it the foundation of fast parsing.

### Rules for using views safely

1. **Parameters**: `std::string_view` by value for read-only string parameters. Exception: if the function needs a
   null-terminated C string (to call a C API), take `const std::string&` or a `const char*`, because a view needn't
   be null-terminated.
2. **Return values and members**: only when the viewed characters provably outlive the view, such as a view into
   a buffer the caller owns and passed in, or into a static table.
3. **Never** create a view from a temporary `std::string` and keep it past the full expression.

```cpp
std::string_view venue_of(const Order& o) { return o.venue; }   // fine if o.venue is a std::string member and o outlives the view

std::string_view sym = get_symbol();            // BUG if get_symbol() returns std::string by value
```

> [!TIP]
> GCC 13+ and Clang warn about many of these cases (`-Wdangling-reference`, `-Wdangling-gsl`), and ASan catches
> the rest at run time as `heap-use-after-free` or `stack-use-after-scope`.

---

## 3. Parsing without allocating

Here is a complete zero-allocation FIX field splitter. It walks the message once and hands each tag and value to a
callback as views into the original buffer:

```cpp
template <class OnField>
bool for_each_fix_field(std::string_view msg, OnField&& on_field) {
    while (!msg.empty()) {
        std::size_t eq = msg.find('=');
        if (eq == std::string_view::npos) return false;
        std::size_t soh = msg.find('\x01', eq + 1);
        if (soh == std::string_view::npos) return false;

        int tag = 0;
        auto [p, ec] = std::from_chars(msg.data(), msg.data() + eq, tag);
        if (ec != std::errc{} || p != msg.data() + eq) return false;

        on_field(tag, msg.substr(eq + 1, soh - eq - 1));   // a view: no copy
        msg.remove_prefix(soh + 1);
    }
    return true;
}

// Usage
for_each_fix_field(msg, [&](int tag, std::string_view value) {
    switch (tag) {
        case 55: order.symbol = pack_symbol(value); break;
        case 38: std::from_chars(value.data(), value.data() + value.size(), order.qty); break;
        case 44: order.price = parse_price_ticks(value); break;
    }
});
```

No `std::string` is ever created. Every piece of text is a view into the receive buffer, so the only memory touched
is the message itself, once.

`find` on a `string_view` typically compiles to `memchr`, which glibc implements with SIMD, scanning 32 bytes per
instruction. For very hot parsers, specialized SIMD scanning can go further (Chapter 13.2).

### Exercises

:::exercise Split a CSV line
Write `split_csv(std::string_view line, std::span<std::string_view> out)` that splits on commas into `out` (which
has room for a fixed number of fields) and returns the number of fields found. No allocation.
:::solution
```cpp
std::size_t split_csv(std::string_view line, std::span<std::string_view> out) {
    std::size_t n = 0;
    while (n < out.size()) {
        std::size_t comma = line.find(',');
        out[n++] = line.substr(0, comma);         // substr clamps when comma == npos
        if (comma == std::string_view::npos) break;
        line.remove_prefix(comma + 1);
    }
    return n;
}
```
Each output is a view into `line`, so the caller must keep the line's storage alive while using the fields. The
practice set has a tested version of this, including the empty-field and trailing-comma cases.
:::

---

## 4. Numbers and text: `from_chars` and `to_chars`

The traditional conversions are slow and quirky:

- `std::stoi`, `std::stod` take a `std::string` (so a view must be copied), throw on errors, and depend on the
  global **locale**.
- `atoi`, `strtod` need a null-terminated string, report errors awkwardly, and also consult the locale.
- `std::stringstream` allocates and is much slower than everything else.

C++17's `<charconv>` provides `std::from_chars` and `std::to_chars`: locale-independent, non-allocating,
non-throwing, and working directly on `char` ranges.

```cpp
std::string_view s = "12345";
std::int64_t qty;
auto [ptr, ec] = std::from_chars(s.data(), s.data() + s.size(), qty);
if (ec == std::errc::invalid_argument) { /* not a number */ }
if (ec == std::errc::result_out_of_range) { /* overflow */ }
if (ptr != s.data() + s.size()) { /* trailing characters */ }

char buf[32];
auto [end, ec2] = std::to_chars(buf, buf + sizeof buf, qty);
std::string_view text(buf, end - buf);    // "12345", not null-terminated
```

`from_chars` for floating point (GCC 11+, Clang 17+/libc++) is also exact: it returns the double closest to the
decimal text, and `to_chars` produces the shortest text that round-trips. Both are much faster than `strtod` and
`printf`.

> [!QUANT]
> For prices, prefer not to go through `double` at all. Parse the decimal string straight into integer ticks:
> read the integer part and the fractional digits separately with `from_chars`, then combine them using a table of
> powers of ten (Chapter 3.3). That avoids both the cost of floating-point parsing and representation error, since
> `187.25` is exact as `18725` hundredths but not as a `double` sum of binary fractions. Chapter 14.2 covers
> fixed-point prices.

### Exercises

:::exercise Parse a price into ticks
Write `std::optional<std::int64_t> parse_cents(std::string_view s)` that parses strings like `"187.25"`, `"187.2"` and
`"187"` into cents (18725, 18720, 18700), rejecting anything else. Use `from_chars`.
:::solution
```cpp
std::optional<std::int64_t> parse_cents(std::string_view s) {
    auto all_digits = [](std::string_view t) {
        return std::ranges::all_of(t, [](char c) { return c >= '0' && c <= '9'; });
    };
    std::size_t dot = s.find('.');
    std::string_view whole = s.substr(0, dot);
    std::string_view frac = dot == std::string_view::npos ? std::string_view{} : s.substr(dot + 1);
    if (whole.empty() || frac.size() > 2 || !all_digits(whole) || !all_digits(frac)) return std::nullopt;

    std::int64_t w = 0, f = 0;
    if (std::from_chars(whole.data(), whole.data() + whole.size(), w).ec != std::errc{})
        return std::nullopt;                                   // too many digits: out of range
    if (!frac.empty()) {
        std::from_chars(frac.data(), frac.data() + frac.size(), f);   // 1-2 digits: can't fail
        if (frac.size() == 1) f *= 10;
    }
    return w * 100 + f;
}
```
Checking that both parts are all digits up front matters: `from_chars` accepts a leading `-` for signed types, so
without the check `"187.-5"` would parse. A string like `"187."` gives an empty fraction and is accepted as 18700;
whether to allow it is a protocol decision. A production parser would also guard `w * 100` against overflow.
:::

---

## 5. Building strings efficiently

When you must produce text (log lines, FIX messages, reports):

- `reserve` the final size, then `append`, rather than chaining `+`.
- Reuse one `std::string` buffer across messages: `clear()` keeps its capacity, just like a vector.
- Use `std::format` (C++20) or `fmt::format` for readable formatting. `std::format_to` into a reused buffer, or
  `std::format_to_n` into a fixed `char` array, avoids allocating.
- For FIX and other fixed-structure outputs, write directly into a `char` buffer with `to_chars`.

```cpp
char buf[256];
char* p = buf;
p = std::format_to(p, "35=D\x01" "55={}\x01" "38={}\x01", symbol, qty);   // no allocation
std::string_view out(buf, p - buf);
```

(`format_to` doesn't check the buffer size; use `format_to_n` when the output length isn't bounded.)

---

## Summary

- `std::string` is 32 bytes in libstdc++ with SSO: strings up to 15 characters never touch the heap.
- Long strings allocate on construction, copy, `+` and `substr`; passing literals to `const std::string&` parameters
  builds temporaries.
- `std::string_view` borrows characters without allocating. Use it for parameters and for parsing; be careful
  returning or storing it.
- Zero-allocation parsing: walk a view with `find`, `substr` and `remove_prefix`, and hand out views into the
  original buffer.
- Use `std::from_chars` and `std::to_chars` for number conversions: fast, exact, locale-free, non-throwing.
- Build output with `reserve` + `append`, reused buffers, and `std::format_to`.

## Key terms

**Small-string optimization (SSO)** · **null terminator** · **`std::string_view`** · **zero-allocation parsing** ·
**`std::from_chars`** · **`std::to_chars`** · **locale** · **`std::format`** · **buffer reuse**
