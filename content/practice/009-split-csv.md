+++
title = "Split a CSV Line Without Allocating"
difficulty = "easy"
tags = ["strings", "parsing", "stl"]
+++

Reference data and backtest files are often CSV. Implement:

```cpp
std::size_t split(std::string_view line, char delim, std::span<std::string_view> out);
```

Write each field of `line` into `out` as a `std::string_view` **into the original buffer** (no copies, no
allocation), and return the number of fields. If there are more fields than `out.size()`, fill `out` and return
`out.size()`. An empty line has one empty field. No quoting rules.

**Example:** `"AAPL,189.25,,300"` → `{"AAPL", "189.25", "", "300"}`, returns 4.

## Starter Code

```cpp
#pragma once
#include <cstddef>
#include <span>
#include <string_view>

inline std::size_t split(std::string_view line, char delim, std::span<std::string_view> out) {
    return 0;  // TODO
}
```

## Hints

- `line.find(delim, pos)` returns `std::string_view::npos` when there are no more delimiters.
- `line.substr(pos, len)` on a `string_view` is O(1). It just adjusts a pointer and a length.

## Solution

```cpp
#pragma once
#include <cstddef>
#include <span>
#include <string_view>

inline std::size_t split(std::string_view line, char delim, std::span<std::string_view> out) {
    std::size_t n = 0, pos = 0;
    while (n < out.size()) {
        std::size_t end = line.find(delim, pos);
        if (end == std::string_view::npos) {
            out[n++] = line.substr(pos);
            break;
        }
        out[n++] = line.substr(pos, end - pos);
        pos = end + 1;
    }
    return n;
}
```

Each field is just a (pointer, length) pair, 16 bytes, pointing into the caller's buffer. The caller must keep
that buffer alive for as long as it uses the views: that's the price of zero-copy parsing.

## Tests

```cpp
#include <array>
#include <string>
int main() {
    std::array<std::string_view, 8> f;
    CHECK_EQ(split("AAPL,189.25,,300", ',', f), 4u);
    CHECK(f[0] == "AAPL" && f[1] == "189.25" && f[2].empty() && f[3] == "300");
    CHECK_EQ(split("", ',', f), 1u);
    CHECK(f[0].empty());
    CHECK_EQ(split("a,b,", ',', f), 3u);
    CHECK(f[2].empty());
    std::string line = "x|y|z";
    CHECK_EQ(split(line, '|', f), 3u);
    CHECK(f[1].data() == line.data() + 2);      // a view into the original, not a copy
    std::array<std::string_view, 2> small;
    CHECK_EQ(split("1,2,3,4", ',', small), 2u);
    CHECK(small[0] == "1" && small[1] == "2");
    return judge::report();
}
```
