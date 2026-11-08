+++
hours = 4
+++

## Motivation

Exchanges don't send market data as JSON. They send tightly packed **binary messages**, often over UDP
multicast: fixed layouts, big-endian integers, no delimiters, no field names. NASDAQ's TotalView-ITCH, CME's MDP
3.0 (SBE) and most other venues work this way. A feed handler's first job is to turn those bytes into typed
events, as fast as the network delivers them, without ever falling behind.

Falling behind is the cardinal sin. Bursts at the open or around news events can be 10–100× the average rate.
If your parser can't keep up, the kernel's socket buffer fills, packets are dropped, your book goes stale, and you
spend the most important seconds of the day recovering instead of trading. Parsers are therefore written to do
**zero allocations, zero copies of the input, and almost no branches**.

Parsing also exercises several ideas from the textbook at once: unaligned loads and strict aliasing
(Chapter 1.3), endianness, `memcpy` as the safe way to reinterpret bytes, templates for zero-overhead dispatch to
a handler, and careful benchmarking.

**What you'll practise:** byte-level decoding, `std::span`, `std::byte`, avoiding UB in type punning, static
dispatch through templates, stream framing, and measuring throughput.

---

## The protocol

This project uses a simplified ITCH-style protocol, specified completely here.

- A stream is a sequence of **frames**. Each frame is a 2-byte big-endian **length** *L*, followed by *L* bytes:
  a 1-byte ASCII **message type**, then the message **body**.
- All integers are **unsigned and big-endian** (network byte order). There is no padding and no alignment: a
  `uint64_t` can start at any byte offset.
- Prices are `uint32` in units of 1/10,000 of a dollar. Symbols are 8 ASCII bytes, right-padded with spaces.

| Type | Message | Length *L* | Body fields, in order |
|---|---|---|---|
| `A` | Add Order | 34 | timestamp u64, order_id u64, side char (`B`/`S`), shares u32, symbol char[8], price u32 |
| `X` | Order Cancel | 21 | timestamp u64, order_id u64, cancelled_shares u32 |
| `E` | Order Executed | 29 | timestamp u64, order_id u64, executed_shares u32, match_id u64 |
| `D` | Order Delete | 17 | timestamp u64, order_id u64 |

Here's an Add Order frame on the wire, byte by byte:

```layout "An Add Order frame is 36 bytes: the 2-byte length, the type byte, and a 33-byte body. Note how the u32 and u64 fields start at odd offsets, so they're never naturally aligned."
title: Add Order ('A'), 12 bytes per row
row: 12
len=34:2:6 type='A':1:4 timestamp_(u64):8:1 order_id_(u64):8:2 side:1:4 shares_(u32):4:3 symbol_(char[8]):8:5 price_(u32):4:3
```

And a Delete:

```layout
title: Order Delete ('D'), 12 bytes per row
row: 12
len=17:2:6 type='D':1:4 timestamp_(u64):8:1 order_id_(u64):8:2
```

**Rules for your parser:**

1. Unknown message types are **not errors**. Skip them using the length, and report them to
   `on_unknown(type, body)`. (Real feeds add message types over time.)
2. A **known type with the wrong length**, or a length of 0, is malformed. Stop and set `error = true`.
3. An **incomplete frame** at the end of the buffer isn't an error either. Stop and report how many bytes of
   complete messages you consumed, so the caller can keep the remainder and append the next packet to it.

---

## The API

The full header, a test suite, a wire-format encoder for tests, and a benchmark are in
`starter/projects/itch-parser/`.

```cpp feed.hpp
namespace feed {

template <class T>
T load_be(const std::byte* p);        // unsigned big-endian load from a possibly unaligned address

struct AddOrder      { std::uint64_t timestamp, order_id; char side; std::uint32_t shares;
                       std::array<char, 8> symbol; std::uint32_t price; };
struct OrderCancel   { std::uint64_t timestamp, order_id; std::uint32_t cancelled_shares; };
struct OrderExecuted { std::uint64_t timestamp, order_id; std::uint32_t executed_shares; std::uint64_t match_id; };
struct OrderDelete   { std::uint64_t timestamp, order_id; };

struct ParseResult {
    std::size_t consumed;   // bytes of complete messages consumed
    std::size_t messages;   // complete messages handled (including unknown types)
    bool        error;      // a malformed message stopped parsing
};

// Calls h.on(const AddOrder&), h.on(const OrderCancel&), h.on(const OrderExecuted&),
// h.on(const OrderDelete&), and h.on_unknown(char type, std::span<const std::byte> body).
template <class Handler>
ParseResult parse(std::span<const std::byte> buf, Handler&& h);

}  // namespace feed
```

A handler is any type with those member functions, so there are no virtual calls and the compiler can inline
your handler straight into the parse loop:

```cpp
struct VolumeCounter {
    std::uint64_t added = 0;
    void on(const feed::AddOrder& m)  { added += m.shares; }
    void on(const feed::OrderCancel&) {}
    void on(const feed::OrderExecuted&) {}
    void on(const feed::OrderDelete&) {}
    void on_unknown(char, std::span<const std::byte>) {}
};
```

```bash
cd starter/projects/itch-parser
g++ -std=c++20 -O2 -Wall -Wextra -fsanitize=address,undefined feed_test.cpp -o test && ./test
g++ -std=c++20 -O2 -march=native feed_bench.cpp -o bench && ./bench
```

---

## Implementation

:::step Big-endian loads
Implement `load_be<T>` for `uint16_t`, `uint32_t` and `uint64_t`. It must be correct for **unaligned**
addresses and must not violate strict aliasing. The tests deliberately load from odd offsets, and UBSan will
flag misaligned access.
:::hint
`*reinterpret_cast<const std::uint32_t*>(p)` is undefined behaviour twice over: it breaks strict aliasing, and
`p` may be misaligned. What's the standard-blessed way to copy bytes into an integer? (Chapter 1.3, §5.)
:::hint
`std::memcpy(&v, p, sizeof v)` with a constant size compiles to a single `mov`. Then reverse the byte order.
C++23 has `std::byteswap`. In C++20 use `__builtin_bswap16/32/64`, or shift-and-or the bytes yourself and check
that the compiler recognizes the pattern as a `bswap` or `movbe` instruction.
:::hint
`if constexpr (sizeof(T) == 2) ... else if constexpr (sizeof(T) == 4) ...` picks the right swap at compile
time with no runtime branch.
:::

:::step Framing loop
Write the outer loop of `parse`: while at least 2 bytes remain, read the length; if the whole frame isn't there
yet, stop; otherwise dispatch it and advance by `2 + length`. Return the bytes consumed and messages handled.
Ignore message contents for now: just call `on_unknown` for everything and get `test_partial_frames` passing.
:::hint
Be careful with unsigned arithmetic when checking "is the whole frame present". `remaining - 2 < len` is safe
only if you've already checked `remaining >= 2`. Otherwise `size_t` wraps around to a huge number.
:::

:::step Decode the four message types
`switch` on the type byte. For each known type, check the length matches the spec exactly (else return an
error *without* advancing past the bad frame), decode each field at its offset, and call `h.on(msg)`.
:::hint
Write the field offsets down as named constants, or compute them from the table above. A single off-by-one is
the most common bug here. The test with maximal values (`0xFFFF...`) catches swapped or truncated fields.
:::hint
Body offsets for Add Order, relative to the first byte after the type: timestamp 0, order_id 8, side 16,
shares 17, symbol 21, price 29. Total 33 bytes, plus the type byte gives *L* = 34.
:::

:::step Streaming
Real data arrives in packets that don't line up with message boundaries. Make sure the streaming test passes. It
feeds the stream in odd-sized chunks, and the caller keeps any unconsumed tail. Think about what your function
must guarantee about `consumed` for this to work.
:::hint
`consumed` must always land on a frame boundary, and you must never call the handler for a frame you didn't
fully have. If both hold, the caller's "erase consumed bytes, append next chunk, call again" loop just works.
:::

:::step Measure and optimize
Run the benchmark. A straightforward implementation along these lines parses about **88 million messages per
second (11 ns/msg, 2.4 GB/s)** on the test machine. Now investigate:

1. Look at the assembly for the `'A'` case (`g++ -O2 -S`, or Compiler Explorer). Is each `load_be` a single
   `mov` + `bswap` (or `movbe`)?
2. Is the handler inlined? Try temporarily marking a handler method `[[gnu::noinline]]` and measure the
   difference.
3. Try reordering the `switch` cases, or adding `[[likely]]` to the most common type. Does it change anything?
   Why or why not?
:::hint
For the benchmark's random mix of message types, the `switch` branch is inherently unpredictable: about 4 types
equally likely means a mispredict on roughly 75% of messages. Real feeds are much more skewed (adds and deletes
dominate), so measure with realistic data before you optimize for it.
:::hint
Some feed handlers avoid decoding fields nobody uses: they hand the handler a lightweight "view" object that
decodes each field lazily on access. Try it for `AddOrder` and compare.
:::

---

## Testing

The suite checks byte-order and unaligned loads, every message type, maximum field values, every possible
truncation point of a two-message stream, a 1,000-message stream delivered in irregular chunks, unknown types,
and malformed lengths. Always run it under `-fsanitize=address,undefined`. Out-of-bounds reads at the end of the
buffer are the classic parser bug, and ASan catches them.

## Stretch goals

:::exercise Fuzz it
Compile with Clang's libFuzzer (`-fsanitize=fuzzer,address`) and write a fuzz target that calls `parse` on
arbitrary bytes. Run it for a few minutes. A robust parser must never crash or read out of bounds, however
corrupt its input.
:::hint
```cpp
extern "C" int LLVMFuzzerTestOneInput(const uint8_t* data, size_t size) {
    struct Null { void on(auto const&) {} void on_unknown(char, std::span<const std::byte>) {} } h;
    feed::parse(std::span(reinterpret_cast<const std::byte*>(data), size), h);
    return 0;
}
```
:::

:::exercise Feed the order book
Write a handler that applies messages to your **Limit Order Book** (add → `add`, cancel → `reduce`,
execute → `reduce`, delete → `cancel`). Benchmark the combined pipeline in messages per second. Which half
dominates?
:::

:::exercise Sequence numbers and gaps
Real UDP feeds wrap messages in packets with a sequence number so receivers can detect loss. Design a packet
header (sequence number u64, message count u16), extend the parser to validate it, and report gaps to the
handler through `on_gap(expected, received)`.
:::
