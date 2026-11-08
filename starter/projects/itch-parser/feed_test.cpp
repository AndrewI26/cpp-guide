#include "feed.hpp"
#include "encoder.hpp"
#include "test_util.hpp"
#include <string>
#include <vector>

using namespace feed;

struct Recorder {
    std::vector<AddOrder> adds;
    std::vector<OrderCancel> cancels;
    std::vector<OrderExecuted> execs;
    std::vector<OrderDelete> dels;
    std::string unknown;          // types of unknown messages, in order
    std::size_t unknown_bytes = 0;
    void on(const AddOrder& m) { adds.push_back(m); }
    void on(const OrderCancel& m) { cancels.push_back(m); }
    void on(const OrderExecuted& m) { execs.push_back(m); }
    void on(const OrderDelete& m) { dels.push_back(m); }
    void on_unknown(char t, std::span<const std::byte> body) { unknown += t; unknown_bytes += body.size(); }
};

static void test_load_be() {
    const std::byte b[9] = {std::byte{0xFF}, std::byte{0x01}, std::byte{0x02}, std::byte{0x03}, std::byte{0x04},
                            std::byte{0x05}, std::byte{0x06}, std::byte{0x07}, std::byte{0x08}};
    EXPECT(load_be<std::uint16_t>(b + 1) == 0x0102);                 // unaligned on purpose
    EXPECT(load_be<std::uint32_t>(b + 1) == 0x01020304u);
    EXPECT(load_be<std::uint64_t>(b + 1) == 0x0102030405060708ull);
    EXPECT(load_be<std::uint16_t>(b) == 0xFF01);
}

static void test_each_type() {
    test::Writer w;
    w.add(111, 42, 'B', 300, "AAPL", 1'893'500);
    w.cancel(222, 42, 100);
    w.exec(333, 42, 150, 9'000'000'001ull);
    w.del(444, 42);
    Recorder r;
    auto res = parse(w.buf, r);
    EXPECT(!res.error && res.consumed == w.buf.size() && res.messages == 4);
    EXPECT(r.adds.size() == 1 && r.cancels.size() == 1 && r.execs.size() == 1 && r.dels.size() == 1);
    if (r.adds.size() == 1) {
        const auto& a = r.adds[0];
        EXPECT(a.timestamp == 111 && a.order_id == 42 && a.side == 'B' && a.shares == 300 && a.price == 1'893'500);
        EXPECT(std::string(a.symbol.data(), 8) == "AAPL    ");
    }
    if (r.cancels.size() == 1) EXPECT(r.cancels[0].timestamp == 222 && r.cancels[0].cancelled_shares == 100);
    if (r.execs.size() == 1) EXPECT(r.execs[0].executed_shares == 150 && r.execs[0].match_id == 9'000'000'001ull);
    if (r.dels.size() == 1) EXPECT(r.dels[0].timestamp == 444 && r.dels[0].order_id == 42);
}

static void test_large_values() {
    test::Writer w;
    w.add(0xFFFFFFFFFFFFFFFFull, 0x8000000000000001ull, 'S', 0xFFFFFFFFu, "ABCDEFGH", 0x80000000u);
    Recorder r;
    parse(w.buf, r);
    EXPECT(r.adds.size() == 1);
    if (!r.adds.empty()) {
        EXPECT(r.adds[0].timestamp == 0xFFFFFFFFFFFFFFFFull && r.adds[0].order_id == 0x8000000000000001ull);
        EXPECT(r.adds[0].shares == 0xFFFFFFFFu && r.adds[0].price == 0x80000000u && r.adds[0].side == 'S');
    }
}

static void test_partial_frames() {
    test::Writer w;
    w.add(1, 1, 'B', 1, "X", 1);
    w.del(2, 1);
    std::size_t first = 36;                       // 2-byte length + 34
    for (std::size_t cut = 0; cut < w.buf.size(); ++cut) {
        Recorder r;
        auto res = parse(std::span(w.buf).first(cut), r);
        std::size_t expect_consumed = cut >= first ? first : 0;
        EXPECT(!res.error && res.consumed == expect_consumed && res.messages == (cut >= first ? 1u : 0u));
    }
}

static void test_streaming_across_buffers() {
    test::Writer w;
    for (std::uint64_t i = 0; i < 1000; ++i) {
        if (i % 3 == 0) w.add(i, i, 'B', 10, "MSFT", 100);
        else if (i % 3 == 1) w.exec(i, i - 1, 5, i);
        else w.del(i, i - 2);
    }
    // Feed the stream in odd-sized chunks, carrying leftover bytes into the next call.
    Recorder r;
    std::vector<std::byte> pending;
    std::size_t pos = 0, chunk = 1;
    while (pos < w.buf.size()) {
        std::size_t n = std::min(chunk, w.buf.size() - pos);
        pending.insert(pending.end(), w.buf.begin() + std::ptrdiff_t(pos), w.buf.begin() + std::ptrdiff_t(pos + n));
        pos += n;
        chunk = chunk * 7 % 61 + 1;
        auto res = parse(pending, r);
        EXPECT(!res.error);
        pending.erase(pending.begin(), pending.begin() + std::ptrdiff_t(res.consumed));
    }
    EXPECT(pending.empty());
    EXPECT(r.adds.size() == 334 && r.execs.size() == 333 && r.dels.size() == 333);
    bool ordered = true;
    for (std::size_t i = 0; i < r.adds.size(); ++i) ordered &= r.adds[i].timestamp == 3 * i;
    EXPECT(ordered);
}

static void test_unknown_and_malformed() {
    test::Writer w;
    w.raw('Z', 10);            // unknown: skipped, but reported
    w.del(5, 6);
    w.raw('Q', 0);             // unknown with empty body
    Recorder r;
    auto res = parse(w.buf, r);
    EXPECT(!res.error && res.messages == 3 && res.consumed == w.buf.size());
    EXPECT(r.unknown == "ZQ" && r.unknown_bytes == 10 && r.dels.size() == 1);

    test::Writer bad;
    bad.del(1, 1);
    bad.raw('A', 5);           // known type with the wrong length
    bad.del(2, 2);
    Recorder r2;
    auto res2 = parse(bad.buf, r2);
    EXPECT(res2.error && res2.messages == 1 && res2.consumed == 19 && r2.dels.size() == 1);

    test::Writer zero;
    zero.u16(0);
    Recorder r3;
    EXPECT(parse(zero.buf, r3).error);
}

int main() {
    test_load_be();
    test_each_type();
    test_large_values();
    test_partial_frames();
    test_streaming_across_buffers();
    test_unknown_and_malformed();
    return report();
}
