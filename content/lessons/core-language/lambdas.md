+++
title = "Lambdas and Callables"
minutes = 40
practice_tags = ["lambdas", "templates"]
+++

## Why this chapter matters

A market-data handler lets strategies subscribe to updates with a callback. The first version stores callbacks as
`std::vector<std::function<void(const Quote&)>>`. Profiling shows 8% of the hot path inside `std::function`'s
call operator, and a heap allocation each time a subscriber registers a callback that captures more than a couple
of pointers. A rewrite that knows the callback types at compile time makes the dispatch disappear entirely: the
callbacks are inlined into the loop.

Lambdas are the most convenient way to write small pieces of behaviour in C++: comparison functions, callbacks,
visitors, cleanup actions. Used directly they cost nothing. Wrapped carelessly, they can cost an allocation and an
indirect call each. This chapter explains what a lambda actually *is*, so the costs are obvious.

By the end of this chapter you should be able to:

- write lambdas with each kind of capture, and predict what each captures and when;
- explain what the compiler generates for a lambda, and why calling one is free;
- pass callables to functions without type erasure using templates and `auto` parameters;
- explain what `std::function` costs, and choose an alternative when it matters.

---

## 1. A lambda is an object

```cpp
auto is_marketable = [](const Order& o, std::int64_t best_ask) {
    return o.side == Side::Buy && o.price >= best_ask;
};
bool m = is_marketable(order, 10050);
```

A **lambda expression** creates an object of a unique, unnamed class type, called the **closure type**. The code
above is roughly equivalent to:

```cpp
struct __IsMarketable {
    bool operator()(const Order& o, std::int64_t best_ask) const {
        return o.side == Side::Buy && o.price >= best_ask;
    }
};
auto is_marketable = __IsMarketable{};
```

Calling it is calling a member function whose body the compiler can see, so it's almost always inlined. A lambda
with no captures is an empty class: `sizeof` is 1, and it carries no data at all.

Because each lambda has its own type, two lambdas with identical code are different types:

```cpp
auto a = [] { return 1; };
auto b = [] { return 1; };
static_assert(!std::is_same_v<decltype(a), decltype(b)>);
```

That uniqueness is the key to their performance: the type itself identifies the code, so a template that receives
a lambda knows exactly which function it will call.

### Exercises

:::exercise What does it generate?
Write the struct the compiler roughly generates for `[](double px, double qty) { return px * qty; }`. What is its
`sizeof`?
:::solution
```cpp
struct __Lambda {
    double operator()(double px, double qty) const { return px * qty; }
};
```
It has no data members, so `sizeof` is 1 (every complete object must have a nonzero size). When stored as a member
with `[[no_unique_address]]`, or passed by value, it effectively takes no space and no registers.
:::

---

## 2. Captures

A lambda can use variables from its enclosing scope by **capturing** them. Each capture becomes a data member of
the closure.

```cpp
std::int64_t max_qty = 1000;
const RiskLimits& limits = get_limits();

auto check = [max_qty, &limits](const Order& o) {
    return o.qty <= max_qty && o.qty * o.price <= limits.max_notional;
};
```

| Capture | Meaning | Closure member |
|---|---|---|
| `[x]` | copy `x` when the lambda is created | `T x;` |
| `[&x]` | refer to `x` | `T& x;` (in practice a pointer) |
| `[=]` | copy every local that's used | one member per used variable |
| `[&]` | refer to every local that's used | one reference per used variable |
| `[this]` | capture the `this` pointer | `C* this;` |
| `[*this]` | copy the whole current object (C++17) | `C copy;` |
| `[p = std::move(ptr)]` | **init-capture**: a new member initialized from any expression | whatever the initializer gives |

Captures by copy are taken **when the lambda is created**, not when it's called:

```cpp
int threshold = 10;
auto above = [threshold](int x) { return x > threshold; };
threshold = 100;
above(50);   // true: the lambda has its own copy, still 10
```

By default, the closure's `operator()` is `const`, so copied captures can't be modified. Add `mutable` to allow
it:

```cpp
auto counter = [n = 0]() mutable { return ++n; };
counter(); counter();   // returns 1, then 2: n lives inside the closure
```

### Capture pitfalls

**Dangling references.** A lambda that captures by reference must not outlive what it refers to:

```cpp
std::function<bool(int)> make_filter() {
    int limit = 100;
    return [&limit](int x) { return x < limit; };   // limit dies at return: the lambda dangles
}
```

This is the lambda version of returning a reference to a local, and compilers often don't warn. A rule that
works: **capture by reference only when the lambda is used before the enclosing function returns** (passed to an
algorithm, called immediately). Anything stored (callbacks, thread functions, deferred tasks) should capture by
value or by init-capture.

**`[=]` and `this`.** In a member function, `[=]` captures `this` (a pointer), not a copy of the members. A lambda
stored past the object's lifetime then reads freed memory through `this`. C++20 deprecates implicit `this` capture
via `[=]`; write `[this]` or `[*this]` explicitly so the choice is visible.

**Move-only captures** need init-capture:

```cpp
auto buf = std::make_unique<Buffer>();
pool.submit([buf = std::move(buf)] { process(*buf); });   // the closure now owns the buffer
```

### Exercises

:::exercise What does it print?
```cpp
int a = 1, b = 1;
auto f = [a, &b] { return a + b; };
a = 10; b = 10;
std::printf("%d\n", f());
```
:::solution
`11`. `a` was copied when `f` was created (value 1); `b` is a reference, so it sees the update to 10.
:::

:::exercise The stored callback
A `Strategy` member function registers `feed.subscribe([=](const Quote& q) { on_quote(q); });`. The strategy is
destroyed before the feed. What happens on the next quote, and how should this be designed?
:::solution
`[=]` captured `this` (a pointer). After the strategy is destroyed, the feed calls the lambda, which calls
`on_quote` through a dangling `this`: undefined behaviour, typically a crash or silent corruption. The capture mode
isn't the real problem; lifetime is. Fixes: unsubscribe in the strategy's destructor (an RAII subscription handle
that unsubscribes when destroyed is the cleanest), or guarantee by construction that subscribers outlive the feed.
Capturing `[*this]` would avoid the dangling pointer but would call `on_quote` on a stale *copy*, which is almost
never what you want.
:::

---

## 3. Passing callables without cost

To accept "anything callable" in a function, make the callable's type a template parameter:

```cpp
template <class Pred>
std::size_t count_if_orders(const std::vector<Order>& orders, Pred pred) {
    std::size_t n = 0;
    for (const Order& o : orders)
        if (pred(o)) ++n;
    return n;
}

auto n = count_if_orders(orders, [&](const Order& o) { return o.price >= best_ask; });
```

Each lambda passed in instantiates a separate copy of `count_if_orders` in which `pred(o)` is a direct call to a
known function. It inlines, and the loop optimizes exactly as if you'd written the condition inline. This is how
every standard algorithm works.

C++20 lets you write the same thing more briefly with an **abbreviated function template**:

```cpp
std::size_t count_if_orders(const std::vector<Order>& orders, auto pred);   // same as the template above
```

Constrain it with a concept for better error messages (Chapter 3.4):

```cpp
std::size_t count_if_orders(const std::vector<Order>& orders,
                            std::predicate<const Order&> auto pred);
```

Take callables **by value** by default. Lambdas are usually small (empty, or a few captured references), and by
value avoids an indirection. If a callable might be large or stateful and must not be copied, take it by
forwarding reference (`auto&& f`).

### Generic lambdas

A lambda with an `auto` parameter is a **generic lambda**: its `operator()` is a template.

```cpp
auto print = [](const auto& x) { std::cout << x << '\n'; };
print(42); print(3.14); print("AAPL");    // three instantiations
```

Generic lambdas are the natural way to write visitors for `std::variant` (Chapter 2.6) and helpers that work on
both bid and ask sides of a book, which are often different types.

### Exercises

:::exercise Function pointer vs lambda
`std::sort(v.begin(), v.end(), cmp)` is called once with `bool cmp(const Order&, const Order&)` (a function
pointer) and once with an equivalent lambda. Which is faster, and why?
:::solution
The lambda, often by 2× or more on large inputs. With the lambda, `std::sort` is instantiated with the lambda's
unique type, so every comparison is a direct, inlined call. With a function pointer, the comparator's type is just
`bool(*)(const Order&, const Order&)`; the instantiation can't know which function it points to, so each comparison
is an indirect call unless the optimizer manages to propagate the constant pointer (it sometimes does, but it's not
reliable).
:::

---

## 4. `std::function`: type erasure and its cost

Sometimes you can't use a template: you need to *store* callables of different types in one container, or pass
them across a non-template interface. `std::function<R(Args...)>` holds **any** callable with a matching signature:

```cpp
std::vector<std::function<void(const Quote&)>> subscribers;
subscribers.push_back([&](const Quote& q) { strategy_a.on_quote(q); });
subscribers.push_back(&log_quote);                       // a plain function works too
for (auto& cb : subscribers) cb(quote);
```

This is **type erasure**: the concrete type is hidden behind a uniform interface. Its costs:

- **Indirect call.** Each call goes through a function pointer inside the `std::function`. It can't be inlined,
  and an unpredictable mix of targets costs branch mispredictions.
- **Possible heap allocation.** `std::function` has a small internal buffer, typically 16 bytes in libstdc++. A
  callable larger than that, such as a lambda capturing three references or one `std::string`, is heap-allocated
  when the `std::function` is constructed or copied.
- **Size.** `sizeof(std::function<void()>)` is 32 bytes in libstdc++.
- **Copyability required.** `std::function` must be copyable, so it can't hold a lambda that captures a
  `unique_ptr`. C++23's `std::move_only_function` lifts this restriction.

None of this matters for configuration, set-up code, or callbacks invoked a few times a second. On a hot path
that runs per message, it does.

### Alternatives on the hot path

| Situation | Use |
|---|---|
| The callable type is known where it's used | A template parameter (zero cost) |
| A fixed, small set of handler types | `std::variant` of them plus `std::visit` (Chapter 12.5) |
| A non-owning callback parameter that won't be stored | A **function reference**: a pointer to the object plus a function pointer (`std::function_ref` in C++26) |
| Must store arbitrary callables, but no heap | A fixed-capacity "inplace function" with a larger internal buffer (Chapter 7.6) |

A minimal function reference is short enough to write yourself:

```cpp
template <class Sig> class FunctionRef;

template <class R, class... Args>
class FunctionRef<R(Args...)> {
public:
    template <class F>
        requires (!std::is_same_v<std::remove_cvref_t<F>, FunctionRef>)   // don't hijack copying
    FunctionRef(F&& f) noexcept
        : obj_(const_cast<void*>(static_cast<const void*>(std::addressof(f)))),
          call_([](void* o, Args... a) -> R {
              return (*static_cast<std::remove_reference_t<F>*>(o))(std::forward<Args>(a)...);
          }) {}
    R operator()(Args... a) const { return call_(obj_, std::forward<Args>(a)...); }
private:
    void* obj_;
    R (*call_)(void*, Args...);
};

void for_each_level(const Book& book, FunctionRef<void(const Level&)> f);   // no template, no allocation
```

It's two pointers, never allocates, and is perfect for "call this back during this function": a temporary lambda
passed as the argument lives until the end of the call. It does not own the callable, so it must not be stored.

> [!PERF]
> On a typical x86 server, calling a small lambda through a template costs nothing (it's inlined into
> the loop). Calling it through `std::function` or a function reference costs about 1–2 ns per call when the
> target is predictable, plus whatever the lost inlining prevents (often vectorization). Constructing a
> `std::function` that heap-allocates costs 20–50 ns.

### Exercises

:::exercise Will it allocate?
With libstdc++ (16-byte small buffer), does constructing a `std::function<void()>` from each of these lambdas
heap-allocate? (a) `[]{}`; (b) `[&a, &b]{}`; (c) `[&a, &b, &c]{}`; (d) `[s = std::string("x")]{}`.
:::solution
(a) No: empty. (b) No: two references are 16 bytes, which fits (and the lambda is trivially copyable, as libstdc++
requires for local storage). (c) Yes: 24 bytes doesn't fit. (d) Yes: `std::string` is 32 bytes in libstdc++, and
isn't trivially copyable anyway. The exact threshold is an implementation detail, which is itself a reason not to
rely on `std::function` in latency-sensitive code.
:::

---

## Summary

- A lambda creates an object of a unique closure type with an `operator()`. Calling it directly is free and
  inlinable.
- Captures become data members: by copy (taken at creation), by reference, or by init-capture for moves and new
  values. Use `mutable` to modify copies.
- Capture by reference only for lambdas used before the enclosing function returns. Stored callbacks must not
  capture references or `this` that may die first.
- Pass callables as template parameters (or `auto` parameters) to keep calls direct and inlinable.
- `std::function` type-erases: an indirect call per invocation, and a heap allocation for larger callables. Fine
  off the hot path; on it, use templates, `std::variant`, or a function reference.

## Key terms

**Lambda expression** · **closure type** · **capture** · **init-capture** · **`mutable` lambda** ·
**generic lambda** · **abbreviated function template** · **type erasure** · **`std::function`** ·
**function reference** · **small-buffer optimization**
