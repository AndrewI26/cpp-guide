+++
title = "Error Handling Strategies"
minutes = 40
practice_tags = ["ub", "parsing"]
+++

## Why this chapter matters

An order gateway receives a malformed message from a client. What should happen?

- If the parser throws an exception and nothing catches it, the process terminates and every client is
  disconnected.
- If the parser returns `-1` and the caller forgets to check, a garbage order goes to the exchange.
- If the parser calls `abort()`, same as the first case, but now on purpose.
- If the parser returns a `std::expected<Order, ParseError>`, the caller is forced to look at the error, it's
  rejected cleanly, and the hot path never pays for unwinding.

Error handling is a design decision, not a syntax question. C++ offers several mechanisms with very different
performance profiles and failure modes, and trading systems use all of them, in different places. This chapter
gives you the tools and a framework for choosing.

By the end of this chapter you should be able to:

- distinguish programmer errors from recoverable errors, and handle each appropriately;
- explain what exceptions cost when thrown and when not thrown, and the exception-safety guarantees;
- use `std::expected` and error codes to make errors explicit in the type system;
- decide which strategy belongs on a hot path and which in set-up code.

---

## 1. Two kinds of error

Before choosing a mechanism, decide what kind of error you're dealing with.

**Programmer errors (bugs):** a violated precondition or invariant. An index out of range, a null pointer where the
contract says non-null, a book with a negative quantity at a level. The program is in a state its authors didn't
anticipate, so there's no meaningful way to "handle" it. You want to find out loudly, as close to the cause as
possible.

**Recoverable errors (expected failures):** things that legitimately happen in a correct program. A malformed
message from a counterparty, a rejected order, a file that doesn't exist, a network timeout, a risk check that
fails. The program should handle them and continue.

| | Programmer error | Recoverable error |
|---|---|---|
| Example | `book.level(i)` with `i >= size()` | `parse()` gets a truncated message |
| Correct response | fail fast: `assert`, `std::abort`, log and crash | report to the caller, who decides |
| Mechanism | `assert`, contracts, sanitizers | exceptions, `std::expected`, error codes |
| In production | often compiled out (`NDEBUG`), or a terminate with a core dump | always present |

Mixing these up causes the worst error-handling code: catching `std::out_of_range` to "recover" from a bug, or
`assert`ing on network input so the check vanishes in release builds (Chapter 1.2's `NDEBUG` exercise).

---

## 2. Assertions

```cpp
void PriceLevel::remove(std::int64_t qty) {
    assert(qty > 0 && qty <= total_qty_);   // a bug if violated: callers must not do this
    total_qty_ -= qty;
}
```

`assert` checks in debug builds and disappears when `NDEBUG` is defined. That's perfect for expensive invariant
checks you want during development and testing, but not in the production hot path.

For invariants important enough to check in production, write your own always-on check that logs and terminates:

```cpp
#define CHECK(cond)                                                                    \
    do {                                                                               \
        if (!(cond)) [[unlikely]] check_failed(#cond, __FILE__, __LINE__);             \
    } while (0)

[[noreturn, gnu::cold, gnu::noinline]] void check_failed(const char* expr, const char* file, int line);
```

`[[unlikely]]` and the cold, out-of-line failure function keep the check to a single, well-predicted compare-and-
branch on the hot path. (This is one of the few remaining good uses of a macro: it needs the stringified condition
and the call site's line number. C++20's `std::source_location` can replace the `__FILE__`/`__LINE__` part.)

> [!NOTE]
> C++26 adds **contracts** (`pre`, `post`, `contract_assert`), which standardize this with configurable
> enforcement. Until compilers support them widely, a project-wide `CHECK` macro is the norm.

---

## 3. Exceptions

```cpp
Order parse_order(std::string_view msg) {
    if (msg.size() < kMinOrderLen) throw ParseError("truncated order message");
    // ...
}

try {
    Order o = parse_order(msg);
    gateway.send(o);
} catch (const ParseError& e) {
    reject(msg, e.what());
}
```

When an exception is thrown, the runtime **unwinds** the stack: it walks back through each function, running the
destructors of every live local (this is where RAII from Chapter 2.2 pays off), until it finds a matching `catch`.

### What exceptions cost

Modern compilers on x86-64 Linux use **table-based, "zero-cost" exceptions**:

- **When nothing is thrown**, the cost is close to zero at run time. There are no checks on the normal path. The
  unwinding information lives in separate tables (`.eh_frame`, `.gcc_except_table`) that aren't touched unless an
  exception is thrown. The main costs are larger binaries and some lost optimization around calls that might
  throw.
- **When something is thrown**, it's expensive: allocating the exception object, looking up unwind tables for each
  frame, running destructors, matching `catch` types with RTTI. Typically **1–10 µs**, and it can take a global
  lock in older runtimes. That's thousands of times the cost of returning an error code.

So exceptions are fast for errors that **almost never happen** and slow for errors that happen often. A parser
that throws on every malformed message is fine if malformed messages are rare and terrible if a misbehaving client
can send millions of them (that's also a denial-of-service vector).

> [!PERF]
> Many low-latency shops compile with `-fno-exceptions` for the hot-path components, or ban `throw` there by
> convention. The reasons are the unpredictable cost of a throw, and that code inside a `try` region or calling
> potentially-throwing functions can be harder for the optimizer to rearrange. Set-up, configuration and
> recovery code commonly still use exceptions.

### Exception safety guarantees

When a function exits with an exception, what state is the program in? The standard vocabulary:

| Guarantee | Promise |
|---|---|
| **No-throw** (`noexcept`) | never throws. Required of destructors, swaps, move operations, and deallocation |
| **Strong** | if it throws, the state is as if the call never happened (commit or roll back) |
| **Basic** | if it throws, no resources leak and all invariants hold, but the state may have changed |
| None | if it throws, anything may be broken. Not acceptable |

Every function should provide at least the basic guarantee, which RAII gives you almost for free. The strong
guarantee usually costs a copy: do the work on a copy or in a scratch area, then commit with a `noexcept` swap or
move. Copy-and-swap (Chapter 2.3) is exactly this.

### `noexcept`

`noexcept` on a function is a promise that it won't throw. If it does throw anyway, `std::terminate` is called
immediately, with no unwinding. Mark functions `noexcept` when:

- they're move constructors, move assignments, swaps or destructors (Chapter 2.3 explains why moves matter);
- they genuinely can't fail (simple getters, arithmetic on integers, operations on a pre-allocated structure);
- you'd rather crash than propagate an error from them.

### Exercises

:::exercise Which guarantee?
`void Book::add(const Order& o)` first inserts `o.id` into a hash map (which may allocate and throw
`std::bad_alloc`), then appends the order to a price level's vector (which may also throw). If the second step
throws, what guarantee does `add` provide? How would you make it strong?
:::solution
If the second step throws after the first succeeded, the hash map contains an ID for an order that isn't in any
level, so an invariant ("every ID in the map is in the book") is broken. That's *no* guarantee, not even basic.
To make it strong: do the step that can fail first in a way that's easy to undo, and undo it if the second step
throws (a scope guard that erases the map entry, dismissed on success). Or reserve capacity in both containers
first, so the subsequent inserts can't throw, and only then modify anything.
:::

---

## 4. Error codes and `std::expected`

The alternative to throwing is to **return** the error. The classic C approach is an integer code:

```cpp
int parse_order(const char* buf, std::size_t len, Order* out);   // 0 = success, else an error code
```

The problems: the return value is easy to ignore, the output parameter is awkward, and nothing ties the code to
its meaning. C++23's `std::expected<T, E>` fixes all three. It holds **either** a `T` (success) **or** an `E`
(the error):

```cpp
enum class ParseError : std::uint8_t { Truncated, BadType, BadPrice, BadQty };

std::expected<Order, ParseError> parse_order(std::span<const std::byte> buf) {
    if (buf.size() < kOrderLen) return std::unexpected(ParseError::Truncated);
    Order o = decode(buf);
    if (o.qty <= 0) return std::unexpected(ParseError::BadQty);
    return o;
}

auto result = parse_order(buf);
if (!result) {
    reject(result.error());
    return;
}
send(*result);
```

Like `optional` and `variant`, it stores its contents inline: the size is the larger of `T` and `E`, plus a flag.
Returning one is as cheap as returning the struct itself, and checking it is a single compare-and-branch.

`std::expected` is also `[[nodiscard]]`-friendly: mark your error enum or wrap the call so ignoring the result
warns. And it supports chaining with `and_then`, `transform` and `or_else`:

```cpp
auto r = parse_order(buf)
             .and_then([&](Order o) { return risk.check(o); })    // returns expected<Order, ParseError>
             .transform([&](Order o) { return gateway.send(o); });
```

> [!NOTE]
> On compilers without C++23 library support, `tl::expected` is a widely used single-header implementation with
> the same interface. Before `expected`, codebases used `std::error_code` (from `<system_error>`) or returned a
> `std::pair<T, Error>`.

### Error codes vs exceptions, measured

The tradeoff has a clear shape:

- **Success path.** Exceptions: no checks at all. `expected`: one well-predicted branch per call level. Roughly a
  tie; exceptions can be marginally faster for deep call chains where errors are vanishingly rare.
- **Failure path.** Exceptions: microseconds. `expected`: the same cost as the success path, a few nanoseconds.

So the choice depends on how often errors happen and how much the *worst case* matters. On a trading hot path the
worst case is what matters, which is why returned errors dominate there.

### Exercises

:::exercise Choose a strategy
For each, choose `assert`/`CHECK`, an exception, or `std::expected`, and justify it:
(a) loading a config file at start-up, and the file is missing;
(b) a binary feed message with an unknown message type;
(c) `PriceLevel::remove` called with more quantity than the level holds;
(d) an order rejected by a pre-trade risk check.
:::solution
(a) Exception: it happens at start-up, rarely, and the right response is usually to unwind to `main`, log, and
exit. The throw's cost is irrelevant. (b) `expected` (or just counting and skipping): it's input from the outside
world, it can happen at high rates, and it's on the hot path. (c) `assert` or `CHECK`: it's a bug in our own code,
not an input condition; the book's invariant is already broken. (d) `expected`: a risk rejection is a normal,
frequent business outcome on the hot path, not an exceptional condition.
:::

:::exercise The cost of a throw
Write a benchmark that calls a function 1,000,000 times, where 1% of calls fail. Implement the failure once by
throwing and catching an exception, and once by returning `std::expected`. Predict the ratio before running it.
:::solution
With 10,000 throws at roughly 1–2 µs each, the exception version spends about 10–20 ms on failures alone, while the
`expected` version runs the whole million calls in about 1–3 ms. Expect the exception version to be 5–20× slower
overall at a 1% failure rate. At a 0.0001% failure rate the two become indistinguishable. That's the point: the
right choice depends on how often failure happens.
:::

---

## 5. A policy for a trading system

A coherent policy, typical of low-latency codebases:

1. **Bugs:** `assert` for expensive checks in debug builds; an always-on `CHECK` for cheap invariants whose
   violation would be catastrophic (sending an order with a corrupt price). Failure terminates with a core dump.
2. **Hot path:** no `throw`. Recoverable errors are returned as `std::expected` or simple status enums, marked
   `[[nodiscard]]`. Functions are `noexcept` where they can't fail.
3. **Start-up, configuration, tooling:** exceptions are fine and make the code simpler. Catch them at the top level
   and exit cleanly with a clear message.
4. **Boundaries:** at the edge of a component (a thread entry point, a callback from a C library), catch
   everything, log it, and convert it to the component's error convention. Never let an exception propagate out of
   a thread function: that calls `std::terminate`.

> [!IMPORTANT]
> Whatever you choose, **never ignore errors silently**. A swallowed error in a trading system shows up later as
> unexplained P&L, a stuck order, or a position nobody knew about. The type system can enforce most of this:
> `[[nodiscard]]`, `std::expected`, and `-Werror` in CI.

---

## Summary

- Separate **programmer errors** (fail fast with `assert`/`CHECK`) from **recoverable errors** (report to the
  caller).
- Exceptions are nearly free when not thrown and cost microseconds when thrown. Use them for rare errors away from
  the hot path.
- Know the exception-safety guarantees: no-throw, strong, basic. RAII gives basic; commit-or-rollback gives strong.
- `std::expected<T, E>` returns a value or an error, inline, with a cost of one branch. It's the default for
  recoverable errors on hot paths.
- Pick a policy per layer of the system, and never let errors disappear silently.

## Key terms

**Programmer error** · **recoverable error** · **assertion** · **stack unwinding** · **zero-cost exceptions** ·
**exception-safety guarantee** · **`noexcept`** · **error code** · **`std::expected`** · **`std::unexpected`** ·
**fail fast**
