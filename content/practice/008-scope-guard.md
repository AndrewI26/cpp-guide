+++
title = "Scope Guard"
difficulty = "easy"
tags = ["raii", "lambdas", "templates"]
+++

RAII ties cleanup to scope exit. Sometimes the cleanup is ad hoc: "if anything below fails, cancel the order
I just sent". Implement a `ScopeGuard` that runs a callable when it's destroyed, unless `dismiss()` was called:

```cpp
auto guard = make_scope_guard([&] { cancel(order_id); });
risk_check();          // may throw
guard.dismiss();       // success: keep the order
```

Requirements:

- `make_scope_guard(f)` returns a guard holding `f` by value (no `std::function`, no heap allocation).
- The callable runs exactly once on destruction, unless dismissed.
- Guards are **move-only**: copying is disabled, and a moved-from guard must not run the callable.

## Starter Code

```cpp
#pragma once
#include <utility>

template <class F>
class ScopeGuard {
public:
    explicit ScopeGuard(F f) : f_(std::move(f)) {}
    ~ScopeGuard() {}                 // TODO: run f_ unless dismissed
    void dismiss() {}                // TODO
    // TODO: a move constructor that disarms the source, and deleted copy operations
private:
    F f_;
};

template <class F>
ScopeGuard<F> make_scope_guard(F f) { return ScopeGuard<F>(std::move(f)); }
```

## Hints

- You need an `active_` flag.
- The move constructor must deactivate the source guard.
- Mark the destructor `noexcept`. Throwing from a destructor during stack unwinding calls `std::terminate`.

## Solution

```cpp
#pragma once
#include <utility>

template <class F>
class ScopeGuard {
public:
    explicit ScopeGuard(F f) : f_(std::move(f)) {}
    ScopeGuard(ScopeGuard&& other) noexcept : f_(std::move(other.f_)), active_(other.active_) {
        other.active_ = false;
    }
    ScopeGuard(const ScopeGuard&) = delete;
    ScopeGuard& operator=(const ScopeGuard&) = delete;
    ScopeGuard& operator=(ScopeGuard&&) = delete;
    ~ScopeGuard() noexcept { if (active_) f_(); }
    void dismiss() noexcept { active_ = false; }
private:
    F f_;
    bool active_ = true;
};

template <class F>
ScopeGuard<F> make_scope_guard(F f) { return ScopeGuard<F>(std::move(f)); }
```

Because `F` is a template parameter, the lambda is stored inline and its call can be inlined: zero overhead
compared with writing the cleanup by hand. `std::function` would type-erase it, and could allocate.

## Tests

```cpp
#include <stdexcept>
#include <type_traits>
int main() {
    int calls = 0;
    { auto g = make_scope_guard([&] { ++calls; }); }
    CHECK_EQ(calls, 1);
    { auto g = make_scope_guard([&] { ++calls; }); g.dismiss(); }
    CHECK_EQ(calls, 1);
    try { auto g = make_scope_guard([&] { ++calls; }); throw std::runtime_error("x"); } catch (...) {}
    CHECK_EQ(calls, 2);
    {
        auto g1 = make_scope_guard([&] { ++calls; });
        auto g2 = std::move(g1);
    }
    CHECK_EQ(calls, 3);
    CHECK(!std::is_copy_constructible_v<ScopeGuard<void (*)()>>);
    auto lam = [] {};
    CHECK(sizeof(make_scope_guard(lam)) <= 2);   // an empty lambda + a bool: no std::function overhead
    return judge::report();
}
```
