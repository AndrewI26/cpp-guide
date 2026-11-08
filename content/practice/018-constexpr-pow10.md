+++
title = "Compile-Time Power-of-Ten Table"
difficulty = "medium"
tags = ["constexpr", "templates", "numerics"]
+++

Formatting and parsing fixed-point prices constantly needs `10^k`. Computing it at runtime with `std::pow` is
slow (and returns a `double`). Build a lookup table **at compile time**:

```cpp
inline constexpr std::array<std::uint64_t, 20> kPow10 = make_pow10_table();   // 10^0 .. 10^19
constexpr std::uint64_t pow10(unsigned k);           // kPow10[k]
constexpr unsigned digits10(std::uint64_t x);        // number of decimal digits; digits10(0) == 1
```

All three must be usable in `static_assert`. `digits10` must not loop over digits dividing by 10: use the table.

## Starter Code

```cpp
#pragma once
#include <array>
#include <cstdint>

constexpr std::array<std::uint64_t, 20> make_pow10_table() {
    return {};  // TODO
}

inline constexpr std::array<std::uint64_t, 20> kPow10 = make_pow10_table();

constexpr std::uint64_t pow10(unsigned k) { return kPow10[k]; }

constexpr unsigned digits10(std::uint64_t x) {
    return 1;  // TODO
}
```

## Hints

- In C++20, a `constexpr` function can declare a local `std::array`, fill it in a loop, and return it.
- `10^19` fits in a `uint64_t` (max ≈ 1.8 × 10¹⁹), but `10^20` doesn't.
- For `digits10`: count how many table entries are ≤ `x`. A short loop over 20 table entries is fine, or use
  `std::upper_bound`, which is constexpr in C++20.

## Solution

```cpp
#pragma once
#include <algorithm>
#include <array>
#include <cstdint>

constexpr std::array<std::uint64_t, 20> make_pow10_table() {
    std::array<std::uint64_t, 20> t{};
    std::uint64_t v = 1;
    for (auto& e : t) { e = v; v *= 10; }   // the final multiply wraps (unsigned): harmless, never stored
    return t;
}

inline constexpr std::array<std::uint64_t, 20> kPow10 = make_pow10_table();

constexpr std::uint64_t pow10(unsigned k) { return kPow10[k]; }

constexpr unsigned digits10(std::uint64_t x) {
    // number of powers of ten <= x, which is the digit count (with 0 having 1 digit)
    auto it = std::upper_bound(kPow10.begin(), kPow10.end(), x);
    unsigned n = static_cast<unsigned>(it - kPow10.begin());
    return n == 0 ? 1 : n;
}
```

The table is computed by the compiler and stored in the read-only data section. At runtime, `pow10(k)` is a
single load. `digits10` is a 5-step binary search over 20 entries. (An even faster trick estimates digits from
`std::bit_width(x)` times log₁₀2 and corrects with one table comparison.)

## Tests

```cpp
int main() {
    constexpr std::uint64_t ce = pow10(3);      // must be usable at compile time
    constexpr unsigned cd = digits10(999);
    CHECK(ce == 1000 && cd == 3 && pow10(0) == 1);
    CHECK_EQ(kPow10[19], 10000000000000000000ull);
    CHECK_EQ(pow10(9), 1000000000ull);
    CHECK_EQ(digits10(0), 1u);
    CHECK_EQ(digits10(9), 1u);
    CHECK_EQ(digits10(10), 2u);
    CHECK_EQ(digits10(123456789), 9u);
    CHECK_EQ(digits10(18446744073709551615ull), 20u);
    bool ok = true;
    for (unsigned k = 1; k < 20; ++k) ok &= digits10(pow10(k)) == k + 1 && digits10(pow10(k) - 1) == k;
    CHECK(ok);
    return judge::report();
}
```
