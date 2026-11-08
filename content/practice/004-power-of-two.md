+++
title = "Powers of Two"
difficulty = "easy"
tags = ["bit-manipulation"]
+++

Ring buffers, hash tables and allocators all prefer power-of-two sizes, because `x % n` becomes `x & (n - 1)`.
Implement:

```cpp
constexpr bool is_pow2(std::uint64_t x);         // true for 1, 2, 4, ...; false for 0
constexpr std::uint64_t next_pow2(std::uint64_t x);  // smallest power of two >= x; next_pow2(0) == 1
```

Both must be **constexpr** and must not loop over bits. Assume `x <= 2⁶³` for `next_pow2`.

## Starter Code

```cpp
#pragma once
#include <cstdint>

constexpr bool is_pow2(std::uint64_t x) {
    return false;  // TODO
}

constexpr std::uint64_t next_pow2(std::uint64_t x) {
    return 1;  // TODO
}
```

## Hints

- What does `x & (x - 1)` do to the binary representation of `x`?
- C++20's `<bit>` header has `std::bit_width`, `std::bit_ceil` and `std::has_single_bit`. Try implementing these
  yourself first, then compare.

## Solution

```cpp
#pragma once
#include <bit>
#include <cstdint>

constexpr bool is_pow2(std::uint64_t x) {
    return x != 0 && (x & (x - 1)) == 0;   // clearing the lowest set bit leaves 0
}

constexpr std::uint64_t next_pow2(std::uint64_t x) {
    if (x <= 1) return 1;
    return std::uint64_t{1} << std::bit_width(x - 1);   // bit_width = index of highest set bit + 1
}
```

`x & (x - 1)` clears the lowest set bit, so the result is zero exactly when one bit was set. For `next_pow2`,
`bit_width(x - 1)` counts the bits needed to represent `x - 1`. It compiles to a single `lzcnt` instruction.
In production, just use `std::has_single_bit` and `std::bit_ceil`.

## Tests

```cpp
int main() {
    constexpr bool ce1 = is_pow2(64);             // must be usable at compile time
    constexpr std::uint64_t ce2 = next_pow2(17);
    CHECK(ce1 && ce2 == 32);
    CHECK(is_pow2(1) && is_pow2(64) && !is_pow2(0) && !is_pow2(96));
    CHECK(is_pow2(1ull << 63));
    CHECK(!is_pow2((1ull << 63) + 1));
    CHECK_EQ(next_pow2(0), 1u);
    CHECK_EQ(next_pow2(1), 1u);
    CHECK_EQ(next_pow2(2), 2u);
    CHECK_EQ(next_pow2(3), 4u);
    CHECK_EQ(next_pow2(1000), 1024u);
    CHECK_EQ(next_pow2(1024), 1024u);
    CHECK_EQ(next_pow2((1ull << 62) + 1), 1ull << 63);
    bool ok = true;
    for (std::uint64_t i = 1; i < 100000; ++i) {
        std::uint64_t p = next_pow2(i);
        ok &= is_pow2(p) && p >= i && p / 2 < i;
    }
    CHECK(ok);
    return judge::report();
}
```
