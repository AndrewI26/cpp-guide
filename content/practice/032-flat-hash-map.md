+++
title = "Open-Addressing Hash Map for Order IDs"
difficulty = "hard"
tags = ["data-structures", "memory", "cache"]
+++

`std::unordered_map` allocates a node for every element, and every lookup chases at least one pointer. For
order-ID → slot lookups on the hot path, write a **flat** hash map: one contiguous array of slots, using
**linear probing**.

```cpp
class FlatMap {          // uint64 keys -> uint32 values; key 0 is reserved and never inserted
public:
    explicit FlatMap(std::size_t expected);       // pre-size so the load factor stays <= 0.5
    bool insert(std::uint64_t key, std::uint32_t value);   // false if the key already exists
    std::uint32_t* find(std::uint64_t key);                // nullptr if absent
    bool erase(std::uint64_t key);                         // false if absent
    std::size_t size() const;
};
```

Requirements:

- Capacity is a power of two, at least `2 * expected`. Grow (rehash to double) if the load factor would exceed
  0.5.
- Erase must **not** use tombstones. Use **backward-shift deletion**, so later probes still find their keys.
- Hash with a decent mixer, not the identity: exchange IDs are often sequential, which clusters badly.

## Starter Code

```cpp
#pragma once
#include <cstddef>
#include <cstdint>
#include <vector>

class FlatMap {
public:
    explicit FlatMap(std::size_t expected) {}
    bool insert(std::uint64_t key, std::uint32_t value) { return false; }
    std::uint32_t* find(std::uint64_t key) { return nullptr; }
    bool erase(std::uint64_t key) { return false; }
    std::size_t size() const { return 0; }
private:
    struct Slot { std::uint64_t key = 0; std::uint32_t value = 0; };
    std::vector<Slot> slots_;
    std::size_t size_ = 0;
};
```

## Hints

- Mixer: multiply by a large odd constant and take the high bits (Fibonacci hashing), or use the `splitmix64`
  finalizer.
- Probe: `i = hash & mask; while (slots[i].key != 0 && slots[i].key != key) i = (i + 1) & mask;`
- Backward-shift erase: after emptying slot `i`, walk forward. For each occupied slot `j`, if its *home* position
  is not cyclically in `(i, j]`, move it into `i` and continue from `j`. Stop at the first empty slot.

## Solution

```cpp
#pragma once
#include <bit>
#include <cstddef>
#include <cstdint>
#include <vector>

class FlatMap {
public:
    explicit FlatMap(std::size_t expected)
        : slots_(std::bit_ceil(std::max<std::size_t>(16, expected * 2))), mask_(slots_.size() - 1) {}

    bool insert(std::uint64_t key, std::uint32_t value) {
        if ((size_ + 1) * 2 > slots_.size()) grow();
        std::size_t i = home(key);
        while (slots_[i].key != 0) {
            if (slots_[i].key == key) return false;
            i = (i + 1) & mask_;
        }
        slots_[i] = {key, value};
        ++size_;
        return true;
    }

    std::uint32_t* find(std::uint64_t key) {
        for (std::size_t i = home(key);; i = (i + 1) & mask_) {
            if (slots_[i].key == key) return &slots_[i].value;
            if (slots_[i].key == 0) return nullptr;
        }
    }

    bool erase(std::uint64_t key) {
        std::size_t i = home(key);
        while (slots_[i].key != key) {
            if (slots_[i].key == 0) return false;
            i = (i + 1) & mask_;
        }
        // backward-shift: pull later members of the probe chain into the hole
        std::size_t j = i;
        while (true) {
            j = (j + 1) & mask_;
            if (slots_[j].key == 0) break;
            std::size_t h = home(slots_[j].key);
            bool h_in_range = (i <= j) ? (i < h && h <= j) : (i < h || h <= j);   // h in (i, j] cyclically
            if (!h_in_range) { slots_[i] = slots_[j]; i = j; }
        }
        slots_[i] = {};
        --size_;
        return true;
    }

    std::size_t size() const { return size_; }

private:
    struct Slot { std::uint64_t key = 0; std::uint32_t value = 0; };

    std::size_t home(std::uint64_t k) const {
        k ^= k >> 33; k *= 0xff51afd7ed558ccdULL;      // murmur3 finalizer
        k ^= k >> 33; k *= 0xc4ceb9fe1a85ec53ULL;
        k ^= k >> 33;
        return static_cast<std::size_t>(k) & mask_;
    }
    void grow() {
        std::vector<Slot> old = std::move(slots_);
        slots_.assign(old.size() * 2, Slot{});
        mask_ = slots_.size() - 1;
        size_ = 0;
        for (const Slot& s : old) if (s.key) insert(s.key, s.value);
    }

    std::vector<Slot> slots_;
    std::size_t mask_;
    std::size_t size_ = 0;
};
```

A lookup is a hash plus one or two adjacent slot reads, usually within a single cache line, with no allocation
per element. Backward-shift deletion keeps probe chains short forever. Tombstones would gradually fill the table
and slow lookups until the next rehash. Keeping the load factor at or below 0.5 keeps linear-probing chains very
short. (Slots are 16 bytes because of padding; packing keys and values into separate arrays is a further
optimization.)

## Tests

```cpp
#include <random>
#include <unordered_map>
int main() {
    FlatMap m(4);
    CHECK(m.insert(1, 10) && m.insert(2, 20) && !m.insert(1, 99));
    CHECK(m.find(1) && *m.find(1) == 10);
    CHECK(!m.find(3));
    *m.find(2) = 21;
    CHECK(*m.find(2) == 21);
    CHECK(m.erase(1) && !m.erase(1) && !m.find(1) && m.size() == 1);
    FlatMap seq(1000);
    bool ok = true;
    for (std::uint64_t k = 1; k <= 100000; ++k) ok &= seq.insert(k, std::uint32_t(k * 3));   // forces growth
    for (std::uint64_t k = 1; k <= 100000; k += 2) ok &= seq.erase(k);
    for (std::uint64_t k = 1; k <= 100000; ++k) {
        auto* v = seq.find(k);
        ok &= (k % 2 == 0) ? (v && *v == k * 3) : (v == nullptr);
    }
    CHECK(ok && seq.size() == 50000);
    FlatMap r(64);
    std::unordered_map<std::uint64_t, std::uint32_t> model;
    std::mt19937_64 rng(11);
    bool ok2 = true;
    for (int i = 0; i < 300000; ++i) {
        std::uint64_t k = 1 + rng() % 3000;
        int op = int(rng() % 3);
        if (op == 0) ok2 &= r.insert(k, std::uint32_t(i)) == model.emplace(k, std::uint32_t(i)).second;
        else if (op == 1) ok2 &= r.erase(k) == (model.erase(k) == 1);
        else { auto* v = r.find(k); auto it = model.find(k); ok2 &= (v == nullptr) == (it == model.end()) && (!v || *v == it->second); }
    }
    CHECK(ok2 && r.size() == model.size());
    return judge::report();
}
```
