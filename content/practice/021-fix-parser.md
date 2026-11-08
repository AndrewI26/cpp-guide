+++
title = "Parse a FIX Message"
difficulty = "medium"
tags = ["parsing", "strings", "finance"]
+++

**FIX** is the text protocol used for order entry by most brokers and many venues. A message is a sequence of
`tag=value` fields, each terminated by the SOH character (`'\x01'`, shown here as `|`):

```text
8=FIX.4.2|9=65|35=D|49=CLIENT|56=BROKER|11=ORD123|55=AAPL|54=1|38=100|44=189.25|10=123|
```

Implement a zero-allocation view over a message:

```cpp
class FixView {
public:
    explicit FixView(std::string_view msg);              // msg uses '\x01' as the delimiter
    bool valid() const;                                   // every field is well-formed "digits=value"
    std::optional<std::string_view> get(int tag) const;   // first occurrence of tag
    std::optional<std::int64_t> get_int(int tag) const;   // value parsed as an integer
    std::size_t field_count() const;
};
```

Store up to 64 fields in a fixed array (`std::array`), not a `std::vector` or `std::map`. A message with more
than 64 fields is invalid.

## Starter Code

```cpp
#pragma once
#include <array>
#include <charconv>
#include <cstdint>
#include <optional>
#include <string_view>

class FixView {
public:
    explicit FixView(std::string_view msg) {}
    bool valid() const { return false; }
    std::optional<std::string_view> get(int tag) const { return std::nullopt; }
    std::optional<std::int64_t> get_int(int tag) const { return std::nullopt; }
    std::size_t field_count() const { return 0; }
};
```

## Hints

- Walk the string once: find `'='`, parse the tag with `std::from_chars`, then find `'\x01'` for the value's end.
- `std::from_chars` doesn't allocate, doesn't throw, and doesn't depend on the locale, which makes it ideal for
  parsing on a hot path.
- Store each field as `{int tag; std::string_view value;}`. A linear scan over 64 fields beats a map at this
  size.

## Solution

```cpp
#pragma once
#include <array>
#include <charconv>
#include <cstdint>
#include <optional>
#include <string_view>

class FixView {
public:
    explicit FixView(std::string_view msg) {
        std::size_t pos = 0;
        while (pos < msg.size()) {
            std::size_t eq = msg.find('=', pos);
            std::size_t soh = msg.find('\x01', pos);
            if (eq == std::string_view::npos || soh == std::string_view::npos || eq > soh || n_ == fields_.size()) {
                valid_ = false; return;
            }
            int tag = 0;
            auto [ptr, ec] = std::from_chars(msg.data() + pos, msg.data() + eq, tag);
            if (ec != std::errc{} || ptr != msg.data() + eq || eq == pos) { valid_ = false; return; }
            fields_[n_++] = {tag, msg.substr(eq + 1, soh - eq - 1)};
            pos = soh + 1;
        }
    }
    bool valid() const { return valid_; }
    std::optional<std::string_view> get(int tag) const {
        for (std::size_t i = 0; i < n_; ++i)
            if (fields_[i].tag == tag) return fields_[i].value;
        return std::nullopt;
    }
    std::optional<std::int64_t> get_int(int tag) const {
        auto v = get(tag);
        if (!v) return std::nullopt;
        std::int64_t out = 0;
        auto [ptr, ec] = std::from_chars(v->data(), v->data() + v->size(), out);
        if (ec != std::errc{} || ptr != v->data() + v->size()) return std::nullopt;
        return out;
    }
    std::size_t field_count() const { return n_; }
private:
    struct Field { int tag; std::string_view value; };
    std::array<Field, 64> fields_{};
    std::size_t n_ = 0;
    bool valid_ = true;
};
```

The view stores pointers into the caller's buffer, so constructing it costs a single pass and zero allocations.
Production FIX engines go further: they precompute offsets for the handful of tags they care about, and validate
the checksum (tag 10) and body length (tag 9).

## Tests

```cpp
#include <string>
int main() {
    std::string m = "8=FIX.4.2|9=65|35=D|11=ORD123|55=AAPL|54=1|38=100|44=189.25|10=123|";
    for (char& c : m) if (c == '|') c = '\x01';
    FixView v(m);
    CHECK(v.valid());
    CHECK_EQ(v.field_count(), 9u);
    CHECK(v.get(35) == std::optional<std::string_view>("D"));
    CHECK(v.get(55) == std::optional<std::string_view>("AAPL"));
    CHECK(v.get_int(38) == std::optional<std::int64_t>(100));
    CHECK(!v.get_int(44));                 // "189.25" isn't an integer
    CHECK(!v.get(999));
    CHECK(v.get(8)->data() == m.data() + 2);
    CHECK(!FixView(std::string_view("8=FIX.4.2")).valid());          // missing SOH
    CHECK(!FixView(std::string_view("abc=1\x01")).valid());          // non-numeric tag
    CHECK(!FixView(std::string_view("=1\x01")).valid());             // empty tag
    std::string big;
    for (int i = 0; i < 65; ++i) big += "1=x\x01";
    CHECK(!FixView(big).valid());
    CHECK(FixView(std::string_view("")).valid());
    return judge::report();
}
```
