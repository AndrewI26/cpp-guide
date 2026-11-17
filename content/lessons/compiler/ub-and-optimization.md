+++
title = "Undefined Behaviour as an Optimization Contract"
minutes = 40
practice_tags = ["ub", "toolchain"]
+++

## Why this chapter matters

A risk check is supposed to catch quantity overflow before it happens:

```cpp
bool will_overflow(int qty) { return qty + 1 < qty; }
```

GCC 15 at `-O2` compiles it to:

```text
_Z13will_overflowi:
    xor   eax, eax      ; return false
    ret
```

The check is gone. This isn't a compiler bug. Signed integer overflow is **undefined behaviour**, so the compiler may
assume it never happens, and if `qty + 1` never overflows then `qty + 1 < qty` is always false. The same function with
`unsigned` (which wraps by definition) compiles to a real comparison.

Chapter 1.3 introduced undefined behaviour (UB) as a list of things not to do. This chapter looks at it from the
optimizer's side: UB is a **contract**. You promise the program never does certain things; in exchange, the compiler
assumes that promise when optimizing, which enables real speedups. When the promise is broken, the optimizer's
reasoning produces programs that do things no reading of the source would suggest.

By the end of this chapter you should be able to:

- explain how the compiler uses the assumption of no UB to optimize;
- predict how common kinds of UB (signed overflow, null dereference, out-of-bounds access, infinite loops) can
  change or delete code;
- write overflow and bounds checks that the optimizer can't remove;
- use UBSan, warnings and compiler flags to find UB and control its effects.

---

## 1. Why UB enables optimization

The compiler optimizes by proving facts about the program and simplifying using them. UB rules hand it facts for
free:

| Rule | Fact the compiler may assume | Optimization it enables |
|---|---|---|
| signed overflow is UB | `i + 1 > i` for signed `i`; a signed loop counter never wraps | simplifying comparisons; computing trip counts; widening `int` indices to 64-bit pointers without re-extending each iteration |
| dereferencing null is UB | a pointer that was dereferenced isn't null | removing later null checks |
| out-of-bounds access is UB | array indices are in range | removing checks and exits implied by the bounds |
| data races are UB | no other thread modifies non-atomic data | keeping values in registers, reordering loads and stores |
| strict aliasing (Chapter 8.2) | differently typed pointers don't overlap | avoiding reloads, vectorizing |
| loops without side effects terminate (forward-progress rule) | such loops end | deleting loops whose results are unused |

The signed-overflow rule is the most valuable for loops. For `for (int i = 0; i <= n; ++i)`, the compiler knows the
loop runs exactly `n + 1` times (if it could wrap, it might never end), so it can vectorize it, compute the trip
count up front, and use a 64-bit induction variable for the address calculation. With `unsigned i`, wrap-around is
defined, `i <= n` is always true when `n == UINT_MAX`, and some of these transformations are blocked.

---

## 2. What happens when the contract is broken

Because the optimizer reasons *backwards* from the assumption, UB doesn't just produce a wrong value at the point
where it occurs. It can affect code before and after it.

### Deleted null checks

```cpp
int read_then_check(const int* p) {
    int v = *p;              // if p were null, this would be UB...
    if (!p) return -1;       // ...so p isn't null, so this check is dead
    return v;
}
```

GCC 15 at `-O2`:

```text
_Z15read_then_checkPKi:
    mov   eax, DWORD PTR [rdi]
    ret
```

The null check disappeared. This exact pattern caused a well-known Linux kernel vulnerability in 2009 (a `tun`
driver read a field through a pointer before checking it for null, and GCC removed the check), and the kernel has
compiled with `-fno-delete-null-pointer-checks` ever since.

### Out-of-bounds reads that execute silently

```cpp
int table_lookup(int i) {
    static const int t[4] = {10, 20, 30, 40};
    for (int k = 0; k <= 4; ++k)          // bug: <= reads t[4]
        if (k == i) return t[k];
    return 0;
}
```

GCC 15 unrolls this into five comparisons, and for `i == 4` reads whatever follows the table in memory: no crash, no
warning, at `-O2 -Wall -Wextra`. Other compiler versions have compiled closely related loops (a membership test with
the same off-by-one bound) into an unconditional `return true`, reasoning that the loop can't legally run past the
end, so it must have returned earlier. Either way the program is wrong, and *how* it's wrong depends on the compiler
version and flags.

### Time travel

Because UB anywhere on a path makes the whole execution undefined, the compiler may assume a path that leads
unconditionally to UB is never taken, which can change behaviour *before* the UB point:

```cpp
void process(int* p, bool log) {
    if (log) std::puts("processing");
    *p = 42;                              // if p is null, UB here...
}
// ...the compiler may assume p != null throughout, including in code before the store
```

In practice, compilers are careful not to move UB-based reasoning across calls with observable side effects like I/O,
but the principle is why "it printed the log line, so it must have got that far" is not a safe debugging inference
in the presence of UB.

### Exercises

:::exercise What does the optimizer assume?
For each function, describe what the optimizer may assume and how it might simplify the code:
(a) `bool positive_after_add(int x) { return x + 100 > x; }`
(b) `int first(std::vector<int>& v) { int a = v[0]; if (v.empty()) return 0; return a; }`
(c) `void wait_for(const bool& ready) { while (!ready) {} }` (with `ready` set by another thread)
:::solution
(a) Signed overflow can't happen, so `x + 100 > x` is always true: `return true`. (b) `v[0]` on an empty vector is
UB, so after the access the vector is assumed non-empty and the `empty()` check may be removed: the function becomes
`return v[0];`. (c) Data races are UB, so the compiler may assume no other thread changes `ready` during the loop,
load it once, and turn the loop into an infinite loop (or delete it if `ready` is true). `ready` must be a
`std::atomic<bool>` (Chapter 11.1).
:::

---

## 3. Writing checks the optimizer can't remove

The rule: **check before the UB happens, using operations that can't themselves be UB.**

**Overflow checks.** Don't compute the overflowing value and then test it. Compare against the limits first, or use
the compiler builtins, which compute the result and report overflow without UB:

```cpp
bool safe_add(std::int64_t a, std::int64_t b, std::int64_t& out) {
    return !__builtin_add_overflow(a, b, &out);          // GCC/Clang: compiles to add + jo
}

bool would_overflow(std::int64_t a, std::int64_t b) {    // portable
    return (b > 0 && a > INT64_MAX - b) || (b < 0 && a < INT64_MIN - b);
}
```

C++26 standardizes this as `std::add_sat`, `std::mul_sat` (saturating) and `<stdckdint.h>`'s `ckd_add` from C23,
which GCC and Clang also provide in C++.

**Null checks.** Check before dereferencing, never after.

**Bounds checks.** Check the index against the size before indexing, or use `.at()`, or iterate with ranges so there's
no index to get wrong.

**Shared data.** Use `std::atomic` for anything one thread writes and another reads (Part III).

> [!QUANT]
> Position and notional arithmetic in trading systems is a classic place for overflow bugs: `price_ticks * qty` in
> 64 bits overflows at about 9.2 × 10¹⁸, which a fat-fingered quantity times a price in nano-units can reach. Use
> checked multiplication in risk checks and in anything handling external input. The practice problem "Overflow-Safe
> Position Arithmetic" covers this.

---

## 4. Tools and flags

**Find UB:**

- **UBSan** (`-fsanitize=undefined`) instruments signed overflow, invalid shifts, null dereferences, misaligned
  accesses, out-of-bounds indexing of arrays with known bounds, invalid enum values and more (Chapter 1.2). Run tests
  with it always.
- **ASan** catches out-of-bounds and use-after-free memory accesses.
- **`constexpr` evaluation** rejects UB at compile time (Chapter 3.3): a `static_assert` over a `constexpr` test of
  your function is a UB checker.
- **Warnings**: `-Wall -Wextra` plus `-Wnull-dereference`, `-Wstrict-overflow` (noisy) and `-Warray-bounds` catch
  some cases statically.

**Change the contract** (each costs some optimization):

| Flag | Effect |
|---|---|
| `-fwrapv` | signed overflow wraps (two's complement), like unsigned |
| `-ftrapv` | signed overflow traps at run time (slow; prefer UBSan in testing) |
| `-fno-strict-aliasing` | type punning through pointers works (Chapter 8.2) |
| `-fno-delete-null-pointer-checks` | the compiler doesn't remove null checks after dereferences |

Some projects use `-fwrapv` to make overflow behaviour predictable; others prefer to keep the optimization and find
bugs with UBSan. Either way, decide deliberately, and know which your build uses.

> [!IMPORTANT]
> "It works at `-O0`" or "it worked with the last compiler" says nothing about code with UB. The only reliable
> defence is not to have UB, verified by sanitizers in testing and by careful handling of every external input.

---

## Summary

- UB is a contract: the compiler assumes it never happens and optimizes accordingly. That enables real speedups,
  especially from signed-overflow and aliasing assumptions.
- When the contract is broken, checks can be deleted (overflow tests, null checks after dereference), out-of-bounds
  accesses execute silently, and effects can appear before the UB point. Measured: GCC 15 compiled
  `x + 1 < x` to `return false`, and removed a null check after a dereference.
- Check before the UB: compare against limits, use `__builtin_*_overflow`, check pointers and indices first, use
  atomics for shared data.
- Find UB with UBSan, ASan, `constexpr` tests and warnings; change the contract with `-fwrapv` and friends only
  deliberately.

## Key terms

**Undefined behaviour** · **optimization contract** · **signed overflow** · **trip count** ·
**null-check elimination** · **forward-progress rule** · **time travel** · **`__builtin_add_overflow`** ·
**`-fwrapv`** · **UBSan**
