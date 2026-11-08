// Project: Option Pricer. Implement the functions below.
// Build tests:  g++ -std=c++20 -O2 -Wall -Wextra -fsanitize=address,undefined pricer_test.cpp -o test && ./test
#pragma once
#include <cmath>
#include <cstdint>
#include <optional>
#include <span>

namespace pricer {

enum class OptionType { Call, Put };

// European option on a non-dividend-paying stock. All rates are continuously compounded, annualized.
struct Option {
    OptionType type;
    double spot;     // S: current underlying price (> 0)
    double strike;   // K (> 0)
    double rate;     // r: risk-free rate, e.g. 0.05
    double vol;      // sigma: annualized volatility (> 0), e.g. 0.20
    double expiry;   // T: time to expiry in years (> 0)
};

double norm_pdf(double x);     // standard normal density
double norm_cdf(double x);     // standard normal CDF, accurate to ~1e-15

double bs_price(const Option& o);

struct Greeks {
    double delta;   // dV/dS
    double gamma;   // d2V/dS2
    double vega;    // dV/dsigma, per 1.00 of vol (divide by 100 for "per vol point")
    double theta;   // dV/dt (calendar time passing), per year; usually negative
    double rho;     // dV/dr, per 1.00 of rate
};
Greeks bs_greeks(const Option& o);

// Volatility that reproduces `price`. std::nullopt if no such volatility exists
// (price outside the no-arbitrage bounds) or the solver fails to converge.
std::optional<double> implied_vol(OptionType type, double spot, double strike, double rate, double expiry,
                                  double price);

struct McResult {
    double price;
    double std_error;   // standard error of the estimate
};
// Monte Carlo price using `paths` simulated terminal prices from a generator seeded with `seed`.
// With antithetic = true, each normal draw z is also used as -z (paths counts draws of z).
McResult mc_price(const Option& o, std::uint64_t paths, std::uint64_t seed, bool antithetic);

// Price many options at once: out[i] = bs_price(in[i]). in.size() == out.size().
void bs_price_batch(std::span<const Option> in, std::span<double> out);

// ---- Implement below. These stubs compile but fail the tests. ----
inline double norm_pdf(double) { return 0; }
inline double norm_cdf(double) { return 0; }
inline double bs_price(const Option&) { return 0; }
inline Greeks bs_greeks(const Option&) { return {}; }
inline std::optional<double> implied_vol(OptionType, double, double, double, double, double) { return std::nullopt; }
inline McResult mc_price(const Option&, std::uint64_t, std::uint64_t, bool) { return {}; }
inline void bs_price_batch(std::span<const Option>, std::span<double>) {}

}  // namespace pricer
