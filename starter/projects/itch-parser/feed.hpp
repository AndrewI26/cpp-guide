// Project: Binary Market Data Parser. Implement load_be and parse() below.
// Protocol spec: see the project page (content/projects/itch-parser.md).
// Build tests:  g++ -std=c++20 -O2 -Wall -Wextra -fsanitize=address,undefined feed_test.cpp -o test && ./test
#pragma once
#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <span>

namespace feed {

// Read an unsigned big-endian integer of type T (uint16_t, uint32_t or uint64_t) from p.
// p may be unaligned.
template <class T>
T load_be(const std::byte* p);

struct AddOrder {               // type 'A', length 34
    std::uint64_t timestamp;
    std::uint64_t order_id;
    char          side;         // 'B' or 'S'
    std::uint32_t shares;
    std::array<char, 8> symbol; // right-padded with spaces
    std::uint32_t price;        // in 1/10000 dollars
};

struct OrderCancel {            // type 'X', length 21
    std::uint64_t timestamp;
    std::uint64_t order_id;
    std::uint32_t cancelled_shares;
};

struct OrderExecuted {          // type 'E', length 29
    std::uint64_t timestamp;
    std::uint64_t order_id;
    std::uint32_t executed_shares;
    std::uint64_t match_id;
};

struct OrderDelete {            // type 'D', length 17
    std::uint64_t timestamp;
    std::uint64_t order_id;
};

struct ParseResult {
    std::size_t consumed;   // bytes of complete messages consumed
    std::size_t messages;   // complete messages handled (including unknown types)
    bool        error;      // true if a malformed message stopped parsing
};

// Parse as many complete messages from `buf` as possible, calling
//   h.on(const AddOrder&), h.on(const OrderCancel&), h.on(const OrderExecuted&), h.on(const OrderDelete&)
//   h.on_unknown(char type, std::span<const std::byte> body)   // body excludes the type byte
// Stops at the first incomplete frame (not an error: the caller will supply more bytes) or at the
// first malformed message (a known type whose length is wrong, or a length of 0), setting error = true.
template <class Handler>
ParseResult parse(std::span<const std::byte> buf, Handler&& h);

// ---- Implement below. These stubs compile but fail the tests. ----
template <class T>
T load_be(const std::byte*) { return 0; }

template <class Handler>
ParseResult parse(std::span<const std::byte>, Handler&&) { return {0, 0, false}; }

}  // namespace feed
