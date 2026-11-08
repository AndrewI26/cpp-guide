+++
title = "LRU Cache for Reference Data"
difficulty = "medium"
tags = ["data-structures", "stl"]
+++

Looking up instrument reference data (tick size, lot size, currency) from a remote service is slow, so you cache
the most recently used entries. Implement a **least-recently-used** cache with O(1) `get` and `put`:

```cpp
class LruCache {
public:
    explicit LruCache(std::size_t capacity);                 // capacity >= 1
    std::optional<int> get(const std::string& key);          // marks key as most recently used
    void put(const std::string& key, int value);             // insert or update; evicts the LRU entry when full
    std::size_t size() const;
};
```

## Starter Code

```cpp
#pragma once
#include <cstddef>
#include <list>
#include <optional>
#include <string>
#include <unordered_map>

class LruCache {
public:
    explicit LruCache(std::size_t capacity) : cap_(capacity) {}
    std::optional<int> get(const std::string& key) { return std::nullopt; }
    void put(const std::string& key, int value) {}
    std::size_t size() const { return 0; }
private:
    std::size_t cap_;
};
```

## Hints

- Keep a `std::list` of (key, value) ordered from most to least recently used, plus an `unordered_map` from key to
  list iterator.
- `list::splice` moves a node to the front in O(1), without copying or reallocating.

## Solution

```cpp
#pragma once
#include <cstddef>
#include <list>
#include <optional>
#include <string>
#include <unordered_map>
#include <utility>

class LruCache {
public:
    explicit LruCache(std::size_t capacity) : cap_(capacity) {}

    std::optional<int> get(const std::string& key) {
        auto it = index_.find(key);
        if (it == index_.end()) return std::nullopt;
        items_.splice(items_.begin(), items_, it->second);   // move to front
        return it->second->second;
    }

    void put(const std::string& key, int value) {
        if (auto it = index_.find(key); it != index_.end()) {
            it->second->second = value;
            items_.splice(items_.begin(), items_, it->second);
            return;
        }
        if (items_.size() == cap_) {
            index_.erase(items_.back().first);
            items_.pop_back();
        }
        items_.emplace_front(key, value);
        index_[key] = items_.begin();
    }

    std::size_t size() const { return items_.size(); }
private:
    std::size_t cap_;
    std::list<std::pair<std::string, int>> items_;
    std::unordered_map<std::string, std::list<std::pair<std::string, int>>::iterator> index_;
};
```

`std::list` iterators stay valid when other nodes are moved or erased, which is what makes the hash map's stored
iterators safe. This is the textbook design. A latency-sensitive version would replace both containers with
pre-allocated pools and index-linked nodes, since every `put` of a new key here allocates twice.

## Tests

```cpp
int main() {
    LruCache c(2);
    c.put("AAPL", 1);
    c.put("MSFT", 2);
    CHECK(c.get("AAPL") == std::optional<int>(1));   // AAPL now most recent
    c.put("GOOG", 3);                                // evicts MSFT
    CHECK(!c.get("MSFT"));
    CHECK(c.get("GOOG") == std::optional<int>(3));
    c.put("AAPL", 10);                               // update in place
    CHECK(c.get("AAPL") == std::optional<int>(10));
    c.put("TSLA", 4);                                // evicts GOOG
    CHECK(!c.get("GOOG") && c.get("TSLA") == std::optional<int>(4));
    CHECK_EQ(c.size(), 2u);
    LruCache one(1);
    one.put("a", 1); one.put("b", 2);
    CHECK(!one.get("a") && one.get("b") == std::optional<int>(2) && one.size() == 1);
    return judge::report();
}
```
