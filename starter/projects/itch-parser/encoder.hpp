// Test helper: encodes messages in the feed's wire format.
#pragma once
#include "feed.hpp"
#include <string_view>
#include <vector>

namespace feed::test {

struct Writer {
    std::vector<std::byte> buf;
    void u8(std::uint8_t v) { buf.push_back(std::byte{v}); }
    void u16(std::uint16_t v) { u8(std::uint8_t(v >> 8)); u8(std::uint8_t(v)); }
    void u32(std::uint32_t v) { for (int s = 24; s >= 0; s -= 8) u8(std::uint8_t(v >> s)); }
    void u64(std::uint64_t v) { for (int s = 56; s >= 0; s -= 8) u8(std::uint8_t(v >> s)); }
    void sym(std::string_view s) { for (int i = 0; i < 8; ++i) u8(i < int(s.size()) ? std::uint8_t(s[i]) : std::uint8_t(' ')); }

    void add(std::uint64_t ts, std::uint64_t id, char side, std::uint32_t sh, std::string_view s, std::uint32_t px) {
        u16(34); u8('A'); u64(ts); u64(id); u8(std::uint8_t(side)); u32(sh); sym(s); u32(px);
    }
    void cancel(std::uint64_t ts, std::uint64_t id, std::uint32_t sh) { u16(21); u8('X'); u64(ts); u64(id); u32(sh); }
    void exec(std::uint64_t ts, std::uint64_t id, std::uint32_t sh, std::uint64_t m) { u16(29); u8('E'); u64(ts); u64(id); u32(sh); u64(m); }
    void del(std::uint64_t ts, std::uint64_t id) { u16(17); u8('D'); u64(ts); u64(id); }
    void raw(char type, std::size_t body_len) { u16(std::uint16_t(body_len + 1)); u8(std::uint8_t(type)); for (std::size_t i = 0; i < body_len; ++i) u8(std::uint8_t(i)); }
};

}  // namespace feed::test
