+++
title = "Decode Big-Endian Fields"
difficulty = "medium"
tags = ["bit-manipulation", "parsing", "ub"]
+++

Exchange protocols send integers in **big-endian** byte order, while x86 is little-endian. Implement, without
undefined behaviour:

```cpp
std::uint16_t load_be16(const unsigned char* p);
std::uint32_t load_be32(const unsigned char* p);
std::uint64_t load_be64(const unsigned char* p);
void store_be32(unsigned char* p, std::uint32_t v);
```

`p` may be **unaligned**, and the tests deliberately use odd addresses. UBSan reports misaligned loads, so
`*reinterpret_cast<const uint32_t*>(p)` fails.

## Starter Code

```cpp
#pragma once
#include <cstdint>
#include <cstring>

inline std::uint16_t load_be16(const unsigned char* p) { return 0; }
inline std::uint32_t load_be32(const unsigned char* p) {
    return *reinterpret_cast<const std::uint32_t*>(p);   // BUG: misaligned, aliasing, wrong byte order
}
inline std::uint64_t load_be64(const unsigned char* p) { return 0; }
inline void store_be32(unsigned char* p, std::uint32_t v) {}
```

## Hints

- Portable version: assemble from bytes, `(uint32_t(p[0]) << 24) | (uint32_t(p[1]) << 16) | ...`. GCC and Clang
  recognize the pattern and emit a single `movbe` or `mov`+`bswap`.
- Alternative: `std::memcpy` into an integer, then `__builtin_bswap32` (or C++23's `std::byteswap`).
- Watch integer promotion: `p[0] << 24` promotes `p[0]` to `int`, and shifting a byte ≥ 0x80 into the sign bit is a
  problem. Cast to the unsigned result type first.

## Solution

```cpp
#pragma once
#include <cstdint>
#include <cstring>

inline std::uint16_t load_be16(const unsigned char* p) {
    return static_cast<std::uint16_t>((std::uint16_t(p[0]) << 8) | p[1]);
}
inline std::uint32_t load_be32(const unsigned char* p) {
    std::uint32_t v;
    std::memcpy(&v, p, 4);          // one unaligned-safe load
    return __builtin_bswap32(v);    // one instruction
}
inline std::uint64_t load_be64(const unsigned char* p) {
    std::uint64_t v;
    std::memcpy(&v, p, 8);
    return __builtin_bswap64(v);
}
inline void store_be32(unsigned char* p, std::uint32_t v) {
    v = __builtin_bswap32(v);
    std::memcpy(p, &v, 4);
}
```

`memcpy` with a constant size is the standard-blessed way to type-pun, and optimizing compilers turn it into a
plain `mov`, since x86 allows unaligned loads. This solution assumes a little-endian host. A portable version
checks `std::endian::native == std::endian::little` (C++20 `<bit>`) and skips the swap on big-endian machines.

## Tests

```cpp
int main() {
    alignas(8) unsigned char buf[16] = {0x00, 0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE, 0xF0, 0xFF, 0x80};
    CHECK_EQ(load_be16(buf + 1), 0x1234);
    CHECK_EQ(load_be32(buf + 1), 0x12345678u);                   // odd address
    CHECK_EQ(load_be64(buf + 1), 0x123456789ABCDEF0ull);
    CHECK_EQ(load_be16(buf + 9), 0xFF80);
    CHECK_EQ(load_be32(buf + 7), 0xDEF0FF80u);
    unsigned char out[7] = {};
    store_be32(out + 3, 0xCAFEBABEu);
    CHECK(out[3] == 0xCA && out[4] == 0xFE && out[5] == 0xBA && out[6] == 0xBE);
    CHECK_EQ(load_be32(out + 3), 0xCAFEBABEu);
    return judge::report();
}
```
