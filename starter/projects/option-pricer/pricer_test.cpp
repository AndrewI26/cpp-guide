#include "pricer.hpp"
#include "test_util.hpp"
#include <cmath>
#include <vector>
#include <algorithm>

using namespace pricer;
static bool near(double a, double b, double tol) { return std::fabs(a - b) <= tol; }

static void test_normal() {
    EXPECT(near(norm_cdf(0.0), 0.5, 1e-15));
    EXPECT(near(norm_cdf(1.96), 0.9750021048517795, 1e-12));
    EXPECT(near(norm_cdf(-1.0), 0.15865525393145707, 1e-12));
    EXPECT(norm_cdf(-40.0) >= 0.0 && norm_cdf(-40.0) < 1e-300 + 1e-200);
    EXPECT(near(norm_cdf(-8.0), 6.220960574271785e-16, 1e-25));   // the far tail needs care
    EXPECT(near(norm_pdf(0.0), 0.3989422804014327, 1e-15));
}

static void test_textbook_values() {
    Option c{OptionType::Call, 100, 100, 0.05, 0.20, 1.0};
    Option p = c; p.type = OptionType::Put;
    EXPECT(near(bs_price(c), 10.450583572185565, 1e-9));
    EXPECT(near(bs_price(p), 5.573526022256971, 1e-9));
    Greeks g = bs_greeks(c);
    EXPECT(near(g.delta, 0.6368306511756191, 1e-9));
    EXPECT(near(g.gamma, 0.018762017345846895, 1e-9));
    EXPECT(near(g.vega, 37.52403469169379, 1e-7));
    EXPECT(near(g.theta, -6.414027546438197, 1e-7));
    EXPECT(near(g.rho, 53.232481545376345, 1e-7));
    Greeks gp = bs_greeks(p);
    EXPECT(near(gp.delta, 0.6368306511756191 - 1.0, 1e-9));
    EXPECT(near(gp.gamma, g.gamma, 1e-12) && near(gp.vega, g.vega, 1e-9));
}

static void test_put_call_parity() {
    bool ok = true;
    for (double K : {60.0, 90.0, 100.0, 115.0, 180.0})
        for (double T : {0.05, 0.5, 2.0})
            for (double vol : {0.05, 0.3, 1.2}) {
                Option c{OptionType::Call, 100, K, 0.03, vol, T}, p = c;
                p.type = OptionType::Put;
                ok &= near(bs_price(c) - bs_price(p), 100 - K * std::exp(-0.03 * T), 1e-9);
            }
    EXPECT(ok);
}

static void test_greeks_vs_finite_differences() {
    bool ok = true;
    for (auto type : {OptionType::Call, OptionType::Put})
        for (double K : {80.0, 100.0, 125.0}) {
            Option o{type, 100, K, 0.04, 0.25, 0.75};
            Greeks g = bs_greeks(o);
            auto bump = [&](auto field, double h) { Option u = o, d = o; u.*field += h; d.*field -= h;
                                                     return (bs_price(u) - bs_price(d)) / (2 * h); };
            Option u = o, d = o; u.spot += 0.01; d.spot -= 0.01;
            double gamma_fd = (bs_price(u) - 2 * bs_price(o) + bs_price(d)) / (0.01 * 0.01);
            ok &= near(g.delta, bump(&Option::spot, 1e-4), 1e-6);
            ok &= near(g.gamma, gamma_fd, 1e-4);
            ok &= near(g.vega, bump(&Option::vol, 1e-5), 1e-4);
            ok &= near(g.rho, bump(&Option::rate, 1e-5), 1e-4);
            ok &= near(g.theta, -bump(&Option::expiry, 1e-5), 1e-4);
        }
    EXPECT(ok);
}

static void test_implied_vol() {
    int fails = 0;
    for (auto type : {OptionType::Call, OptionType::Put})
        for (double K : {50.0, 80.0, 100.0, 120.0, 150.0})
            for (double T : {0.1, 1.0, 3.0})
                for (double vol : {0.05, 0.2, 0.6, 1.5}) {
                    Option o{type, 100, K, 0.02, vol, T};
                    double px = bs_price(o);
                    double df = std::exp(-0.02 * T);
                    double intrinsic = type == OptionType::Call ? std::max(0.0, 100 - K * df) : std::max(0.0, K * df - 100);
                    if (px - intrinsic < 1e-4) continue;   // ~no time value: the price says nothing about vol
                    auto iv = implied_vol(type, 100, K, 0.02, T, px);
                    if (!iv || !near(*iv, vol, 1e-6)) ++fails;
                }
    EXPECT(fails == 0);
    EXPECT(!implied_vol(OptionType::Call, 100, 100, 0.0, 1.0, 101.0));   // above the upper bound (spot)
    EXPECT(!implied_vol(OptionType::Call, 100, 80, 0.0, 1.0, 19.0));     // below intrinsic value
}

static void test_monte_carlo() {
    Option c{OptionType::Call, 100, 105, 0.05, 0.2, 1.0};
    double exact = bs_price(c);
    McResult plain = mc_price(c, 400'000, 42, false);
    McResult anti = mc_price(c, 400'000, 42, true);
    EXPECT(plain.std_error > 0 && plain.std_error < 0.05);
    EXPECT(std::fabs(plain.price - exact) < 4 * plain.std_error);
    EXPECT(std::fabs(anti.price - exact) < 4 * anti.std_error);
    EXPECT(anti.std_error < plain.std_error);
    McResult again = mc_price(c, 400'000, 42, false);
    EXPECT(again.price == plain.price);                                   // reproducible with the same seed
    Option p = c; p.type = OptionType::Put;
    McResult put = mc_price(p, 400'000, 7, true);
    EXPECT(std::fabs(put.price - bs_price(p)) < 4 * put.std_error);
}

static void test_batch() {
    std::vector<Option> opts;
    for (int i = 0; i < 1000; ++i)
        opts.push_back({i % 2 ? OptionType::Call : OptionType::Put, 100, 50.0 + i * 0.1, 0.01, 0.1 + (i % 50) * 0.01, 0.1 + (i % 7) * 0.3});
    std::vector<double> out(opts.size(), -1);
    bs_price_batch(opts, out);
    bool ok = true;
    for (std::size_t i = 0; i < opts.size(); ++i) ok &= near(out[i], bs_price(opts[i]), 1e-12);
    EXPECT(ok);
}

int main() {
    test_normal();
    test_textbook_values();
    test_put_call_parity();
    test_greeks_vs_finite_differences();
    test_implied_vol();
    test_monte_carlo();
    test_batch();
    return report();
}
