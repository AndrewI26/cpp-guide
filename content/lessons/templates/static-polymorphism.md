+++
title = "Static Polymorphism: CRTP and Policies"
minutes = 40
practice_tags = ["static-polymorphism", "templates", "inline"]
+++

## Why this chapter matters

A market-making framework defines a `Strategy` base class with virtual hooks: `on_quote`, `on_trade`,
`on_fill`. A feed handler holds a `Strategy*` and calls `strategy->on_quote(q)` for every update, millions of times
a second. Each call is an indirect call through a vtable. The CPU usually predicts it, so the call itself costs a
few cycles. The real cost is that the compiler can't **inline** across it: whatever `on_quote` does, it can't be
merged into the feed handler's loop, constant-folded or vectorized.

But in a deployed trading binary, the strategy type is known when the binary is built. Nothing is chosen at run
time. Virtual dispatch is paying for flexibility nobody uses.

**Static polymorphism** gets the same code reuse and customization points with all the decisions made at compile
time. This chapter covers the two classic techniques, the **Curiously Recurring Template Pattern** and
**policy-based design**, and when each one is better than virtual functions.

By the end of this chapter you should be able to:

- explain what a virtual call costs, including what it prevents;
- implement CRTP base classes, and explain how they achieve compile-time dispatch;
- compose behaviour from policy classes passed as template parameters;
- choose between virtual functions, CRTP, policies, concepts and `std::variant` for a given design.

---

## 1. What dynamic polymorphism costs

```cpp
class Strategy {
public:
    virtual ~Strategy() = default;
    virtual void on_quote(const Quote& q) = 0;
};

class MarketMaker : public Strategy {
public:
    void on_quote(const Quote& q) override { /* ... */ }
};
```

Each object of a class with virtual functions contains a hidden **vptr** pointing to the class's **vtable**, an
array of function pointers. A call `s->on_quote(q)` compiles to:

```text
mov  rax, [rdi]        ; load the vptr from the object
call [rax + 16]        ; load the function pointer from the vtable and call it
```

The direct costs: two dependent loads (both usually in L1) and an indirect branch that must be predicted. When the
same derived type is always used at a call site, the branch predictor gets it right and the overhead is a couple of
nanoseconds at most.

The indirect costs are usually bigger:

- **No inlining.** The compiler doesn't know which function will run, so it can't inline it, and so it can't
  optimize across the call boundary (Chapter 2.1).
- **8 bytes per object** for the vptr, which matters for small objects stored in large numbers.
- **Heap allocation and pointer chasing.** Polymorphic objects are usually held through pointers
  (`std::vector<std::unique_ptr<Base>>`), scattering them across memory.

> [!NOTE]
> Compilers can sometimes **devirtualize**: if they can prove the dynamic type (the object was just constructed
> locally, or the class or function is `final`), they call the function directly and can inline it. Marking
> leaf classes `final` is cheap and helps. Chapter 8.1 covers devirtualization.

---

## 2. The Curiously Recurring Template Pattern

In CRTP, a class derives from a base template **instantiated with itself**:

```cpp
template <class Derived>
class StrategyBase {
public:
    void handle_quote(const Quote& q) {
        if (q.bid_qty == 0 || q.ask_qty == 0) return;     // shared logic
        ++quotes_seen_;
        derived().on_quote(q);                            // customization point: a direct call
    }
    std::uint64_t quotes_seen() const { return quotes_seen_; }

private:
    Derived& derived() { return static_cast<Derived&>(*this); }
    std::uint64_t quotes_seen_ = 0;
};

class MarketMaker : public StrategyBase<MarketMaker> {
public:
    void on_quote(const Quote& q) { /* quote around the mid */ }
};
```

Inside `StrategyBase<MarketMaker>`, the base knows the derived type at compile time, so `static_cast<Derived&>`
is safe (the object really is a `MarketMaker`) and free (no run-time check). `derived().on_quote(q)` is an
ordinary direct call to `MarketMaker::on_quote`, fully inlinable.

The code that uses it is a template over the strategy type:

```cpp
template <class S>
class FeedHandler {
public:
    explicit FeedHandler(S& strategy) : strategy_(strategy) {}
    void on_packet(std::span<const Quote> quotes) {
        for (const Quote& q : quotes) strategy_.handle_quote(q);   // everything inlined into this loop
    }
private:
    S& strategy_;
};
```

What you give up compared to virtuals:

- There's no common base type. `StrategyBase<MarketMaker>` and `StrategyBase<Arbitrage>` are unrelated, so you
  can't put them in one container or choose between them at run time.
- Every consumer of the strategy becomes a template, with the compile-time and code-size costs from Chapter 3.1.
- A missing `on_quote` in the derived class is a compile error deep in the base's code (concepts help; see
  section 4).

### CRTP for mixins

CRTP is also used to inject functionality into a class, a **mixin**. For example, a base that provides comparison
operators from a single `key()` function, or instrumentation:

```cpp
template <class Derived>
struct Counted {
    static inline std::atomic<std::int64_t> live{0};
    Counted()  { ++live; }
    Counted(const Counted&) { ++live; }
    ~Counted() { --live; }
};

struct Order : Counted<Order> { /* ... */ };   // Order::live tracks how many Orders exist
```

Each `Counted<X>` is a distinct class, so each derived type gets its own counter.

> [!TIP]
> C++23's **deducing this** (`void handle_quote(this auto& self, const Quote& q)`) gives the base access to the
> derived type without the template parameter, which makes many CRTP patterns simpler. Compiler support arrived in
> GCC 14 and Clang 18.

### Exercises

:::exercise Why `static_cast`?
In `StrategyBase<Derived>::derived()`, why is `static_cast<Derived&>(*this)` safe here, and what goes wrong if
someone writes `class Arb : public StrategyBase<MarketMaker>`?
:::solution
It's safe because by convention `Derived` is the class inheriting from this base, so `*this` really is the base
subobject of a `Derived`. If `Arb` mistakenly inherits from `StrategyBase<MarketMaker>`, the cast claims an `Arb` is
a `MarketMaker`, which is undefined behaviour: calls to `on_quote` would operate on the wrong layout. A common guard
is to make the base's constructor private and declare `friend Derived;`, so only the correct derived class can
construct it.
:::

---

## 3. Policy-based design

A **policy** is a class that implements one aspect of behaviour, passed as a template parameter. The host class
composes several of them:

```cpp
struct NoRisk         { bool allow(const Order&) const { return true; } };
struct MaxQtyRisk     { std::int64_t max_qty; bool allow(const Order& o) const { return o.qty <= max_qty; } };

struct NullLogger     { void log(const Order&) {} };
struct BinaryLogger   { void log(const Order& o); /* writes to a ring buffer */ };

template <class RiskPolicy, class LogPolicy>
class OrderGateway {
public:
    OrderGateway(RiskPolicy risk, LogPolicy log) : risk_(risk), log_(log) {}

    bool send(const Order& o) {
        if (!risk_.allow(o)) return false;
        log_.log(o);
        return transmit(o);
    }
private:
    [[no_unique_address]] RiskPolicy risk_;
    [[no_unique_address]] LogPolicy  log_;
    bool transmit(const Order& o);
};

using BacktestGateway = OrderGateway<NoRisk, NullLogger>;
using LiveGateway     = OrderGateway<MaxQtyRisk, BinaryLogger>;
```

`OrderGateway<NoRisk, NullLogger>::send` compiles down to just `transmit(o)`: the empty `allow` and `log` calls are
inlined and vanish. The live version gets real checks. One source, two binaries, each with only the code it needs.

`[[no_unique_address]]` (C++20) lets an empty policy member take zero bytes, so stateless policies cost no space
either. (Before C++20 the same effect came from inheriting from the policy, the "empty base optimization".)

The standard library uses policies everywhere: the allocator in `std::vector<T, Alloc>`, the hash and equality in
`std::unordered_map<K, V, Hash, Eq>`, the deleter in `std::unique_ptr<T, D>`, the comparator in `std::map`.

> [!PERF]
> Policies are the main way low-latency code stays both configurable and fast. A typical pattern: the production
> build and the backtest build instantiate the same engine template with different clock, logging and order-entry
> policies. The backtest clock reads simulated time; the live one reads the TSC. Neither pays for the other.

### Exercises

:::exercise Size with empty policies
What's `sizeof(OrderGateway<NoRisk, NullLogger>)` with and without `[[no_unique_address]]` on the members
(assuming no other members)?
:::solution
With `[[no_unique_address]]`, both empty members can share an address with each other (they're different types)
and take no space, so the class has `sizeof` 1 (every complete object must be at least 1 byte). Without it, each
empty member takes 1 byte, so `sizeof` is 2. With a real 8-byte member alongside, the difference is 8 vs 16
bytes because of alignment padding.
:::

---

## 4. Choosing a form of polymorphism

C++ offers several ways to write code that works with different types. In modern C++, the tradeoffs look like this:

| Technique | Dispatch | Set of types | Heterogeneous container? | Typical use |
|---|---|---|---|---|
| Virtual functions | run time, indirect call | open | yes (via pointers) | plug-ins, types chosen from config, large rarely-called interfaces |
| `std::variant` + `visit` | run time, jump on index | closed | yes (by value) | messages, events, order types |
| CRTP | compile time | open | no | shared skeleton with customization hooks |
| Policies | compile time | open | no | orthogonal configurable aspects |
| Concepts + templates | compile time | open | no | generic algorithms and components |

A common modern alternative to CRTP for "the skeleton calls the customization" is just a template constrained by
a concept, with no base class at all:

```cpp
template <class S>
concept QuoteStrategy = requires(S& s, const Quote& q) { s.on_quote(q); };

template <QuoteStrategy S>
class FeedHandler { /* calls strategy_.on_quote(q) directly */ };
```

Use CRTP when the base genuinely provides shared state and logic that wraps the derived class's hooks; use a
concept-constrained template when it doesn't.

> [!IMPORTANT]
> Don't reach for static polymorphism by reflex. Virtual calls are cheap when they're not in a tight loop, and
> they keep code simple and compile times short. Measure first (Chapter 6). The usual pattern is virtual (or
> `std::function`) at the configuration level, chosen once at start-up, and static dispatch inside the hot loop.

### Exercises

:::exercise Pick one
Choose a technique for each: (a) handling ITCH messages of 20 known types; (b) a strategy loaded by name from a
config file in a research platform; (c) an order book that's sometimes built with a pool allocator and sometimes
with `std::allocator`; (d) a family of indicators (EMA, VWAP, RSI) that share update-and-cache logic and are each
used in a tight loop.
:::solution
(a) `std::variant` + visit: a closed set of message types, stored by value. (b) Virtual functions: the type is
chosen at run time from data, the set is open, and calls aren't per-tick-critical in a research setting. (c) A
policy (an allocator template parameter), exactly as the standard containers do. (d) CRTP, or a concept-constrained
template: the shared skeleton calls a per-indicator hook, and the types are known at compile time so the calls
should inline.
:::

---

## Summary

- A virtual call costs two loads and an indirect branch, but mainly it blocks inlining. Objects also carry a vptr
  and usually live behind pointers.
- **CRTP**: `class D : public Base<D>`. The base calls `static_cast<D&>(*this).hook()`, a direct, inlinable call.
  It gives up a common base type and run-time choice.
- **Policies**: template parameters supplying one aspect of behaviour each. Empty policies cost nothing with
  `[[no_unique_address]]`, and dead features compile away.
- Choose among virtuals, variant, CRTP, policies and concepts based on whether the set of types is open or closed
  and whether choice happens at build time or run time.
- Use static dispatch inside hot loops, and dynamic dispatch at configuration boundaries.

## Key terms

**Dynamic polymorphism** · **vtable** · **vptr** · **devirtualization** · **static polymorphism** · **CRTP** ·
**mixin** · **deducing this** · **policy-based design** · **`[[no_unique_address]]`** · **empty base optimization**
