+++
hours = 5
+++

## Motivation

An options desk prices and risk-manages thousands of instruments continuously. Every time the underlying moves,
every option's price and **Greeks** (sensitivities to spot, volatility, time and rates) must be recomputed so
that quotes stay accurate and hedges stay right. At the same time the desk is running the inverse problem,
backing out **implied volatility** from market prices, to build the volatility surface that everything else is
priced from.

The Black–Scholes formula is the workhorse here. It's a closed-form expression, but it calls `log`, `exp`, `sqrt`
and the normal CDF, each costing tens of nanoseconds. Multiply by thousands of options and dozens of Greeks and
bumps, and pricing speed becomes a real constraint. **Monte Carlo** is the general-purpose fallback for products
with no closed form, and it's an exercise in throughput, random number generation and statistics.

This project is mostly about **getting numbers right** (Part V), then about making correct numerical code fast
(Part II). You'll verify your pricer three independent ways: textbook values, put–call parity, and finite
differences.

**What you'll practise:** numerically careful code, special functions, root finding with safeguards, testing
numerical code properly, Monte Carlo with error bars, reproducible random numbers, and structuring data for
vectorization.

---

## The maths you need

For a European call and put on a non-dividend stock with spot *S*, strike *K*, rate *r*, volatility *σ* and
time to expiry *T*:

```text
d1 = [ ln(S/K) + (r + σ²/2)·T ] / (σ·√T)
d2 = d1 − σ·√T

Call = S·N(d1) − K·e^(−rT)·N(d2)
Put  = K·e^(−rT)·N(−d2) − S·N(−d1)

N(x) = standard normal CDF,   n(x) = e^(−x²/2) / √(2π)
```

| Greek | Call | Put |
|---|---|---|
| Delta ∂V/∂S | N(d1) | N(d1) − 1 |
| Gamma ∂²V/∂S² | n(d1) / (S·σ·√T) | same |
| Vega ∂V/∂σ | S·n(d1)·√T | same |
| Theta ∂V/∂t | −S·n(d1)·σ/(2√T) − r·K·e^(−rT)·N(d2) | −S·n(d1)·σ/(2√T) + r·K·e^(−rT)·N(−d2) |
| Rho ∂V/∂r | K·T·e^(−rT)·N(d2) | −K·T·e^(−rT)·N(−d2) |

Under the risk-neutral measure the terminal price is lognormal, which is what Monte Carlo simulates:

```text
S_T = S · exp( (r − σ²/2)·T + σ·√T·Z ),   Z ~ N(0, 1)
price ≈ e^(−rT) · mean( payoff(S_T) )
```

---

## The API

The starter files are in `starter/projects/option-pricer/`: the header, tests and a benchmark.

```cpp pricer.hpp
namespace pricer {

enum class OptionType { Call, Put };

struct Option {
    OptionType type;
    double spot, strike, rate, vol, expiry;   // S, K, r, sigma, T (years)
};

double norm_pdf(double x);
double norm_cdf(double x);                    // accurate to ~1e-15, including far tails

double bs_price(const Option& o);

struct Greeks { double delta, gamma, vega, theta, rho; };   // vega/rho per 1.00, theta per year
Greeks bs_greeks(const Option& o);

std::optional<double> implied_vol(OptionType type, double spot, double strike, double rate,
                                  double expiry, double price);

struct McResult { double price; double std_error; };
McResult mc_price(const Option& o, std::uint64_t paths, std::uint64_t seed, bool antithetic);

void bs_price_batch(std::span<const Option> in, std::span<double> out);

}  // namespace pricer
```

```bash
cd starter/projects/option-pricer
g++ -std=c++20 -O2 -Wall -Wextra -fsanitize=address,undefined pricer_test.cpp -o test && ./test
g++ -std=c++20 -O2 -march=native pricer_bench.cpp -o bench && ./bench
```

---

## Implementation

:::step The normal distribution
Implement `norm_pdf` and `norm_cdf`. The test checks `N(−8) ≈ 6.22 × 10⁻¹⁶` to *relative* precision, so naive
approaches that compute `1 − something` lose everything in the tail.
:::hint
`<cmath>` has `std::erf` and `std::erfc`. They're related to the normal CDF by a change of variable:
N(x) = ½·erfc(−x/√2).
:::hint
Why `erfc` and not `0.5 * (1 + erf(x / √2))`? For x = −8, `erf` returns −0.9999999999999988 and adding 1
leaves only about one significant digit: **catastrophic cancellation**. `erfc` computes the small tail directly.
:::

:::step Black–Scholes price
Implement `bs_price`. Factor out the shared pieces (`d1`, `d2`, the discount factor, `σ√T`) into a small
helper, since you'll reuse them for the Greeks. Check the textbook value: S = K = 100, r = 5%, σ = 20%, T = 1
gives a call of **10.4506** and a put of **5.5735**.
:::hint
Put–call parity, C − P = S − K·e^(−rT), is an exact identity. The test checks it across a grid of strikes,
expiries and vols. If parity fails but the call is right, the bug is in your put formula.
:::

:::step Greeks
Implement `bs_greeks` from the table above. The tests verify every Greek against **central finite differences**
of your own `bs_price`: bump one input up and down by *h*, and the difference quotient should match your
analytic value.
:::hint
Theta is the change in value as calendar time passes, which *reduces* T. So theta = −∂V/∂T. The test computes it
with exactly that sign.
:::hint
Gamma and vega are the same for calls and puts (parity is linear in S and has no σ), which is a quick sanity
check on your implementation.
:::

:::step Implied volatility
Given a market price, find σ such that `bs_price(σ) = price`. Return `std::nullopt` when no σ can work: the price
must lie strictly between the no-arbitrage bounds, max(0, S − K·e^(−rT)) and S for a call.
:::hint
Newton's method, σ ← σ − (BS(σ) − price) / vega(σ), converges in a handful of iterations near the answer, but
can shoot off to a negative or huge σ when vega is tiny (deep in or out of the money).
:::hint
Make it robust by keeping a bracket [lo, hi] that always contains the root. After each evaluation, shrink the
bracket using the sign of BS(σ) − price, and if a Newton step would leave the bracket, take a bisection step
instead. This "safeguarded Newton" never diverges and is nearly as fast.
:::hint
Start from σ = 0.3 (or a closed-form approximation like Brenner–Subrahmanyam: σ ≈ √(2π/T) · price/S for at-the-money
options), with lo = 1e-6 and hi = 10. Stop when |BS − price| is below ~1e-13 relative to the price.
:::

:::step Monte Carlo
Implement `mc_price`: draw Z with `std::mt19937_64` (seeded with `seed`) and `std::normal_distribution`, compute
each payoff, and return the discounted mean and its **standard error** (sample std dev / √paths). With
`antithetic = true`, use each draw twice, as Z and −Z, and average the pair *before* accumulating statistics.
:::hint
Accumulate `sum` and `sum_sq` in one pass. The variance is (sum_sq/n − mean²)·n/(n−1). For huge path counts,
Welford's algorithm is the numerically safer choice (Chapter 14.3).
:::hint
Antithetic pairs are negatively correlated, so their average has lower variance than two independent draws. But
you must treat each *pair* as one sample when computing the standard error, or you'll underestimate it.
:::

:::step Measure and speed up
Run the benchmark. A straightforward implementation measured on the test machine: **130 ns per `bs_price`**,
and **18 M Monte Carlo paths/s**. Then make `bs_price_batch` substantially faster than calling `bs_price` in a
loop.
:::hint
Profile first: where do the 130 ns go? `log`, `exp`, `erfc` and `sqrt` each cost tens of nanoseconds, and the
`if (type == Call)` branch is unpredictable for a random mix.
:::hint
The branch can go: compute both N(d1) and N(d2), and blend call and put with a ±1 sign
(φ = +1 for calls, −1 for puts: V = φ·[S·N(φ·d1) − K·e^(−rT)·N(φ·d2)]). Branch-free code is also what the
vectorizer needs.
:::hint
To vectorize, convert the array of `Option` structs into a **struct of arrays** (one array per field), and use a
vectorizable math library (glibc's `libmvec`, which GCC uses at `-O3 -ffast-math -march=native`, or a SIMD
library). Measure the accuracy you lose, if any, against the scalar version.
:::

```layout "Array of structs vs struct of arrays: SIMD units want 4 or 8 consecutive spots in one register, which only the SoA layout provides directly."
title: AoS: std::vector<Option> (48 bytes each)
row: 48
type:8:6 spot:8:1 strike:8:2 rate:8:3 vol:8:4 expiry:8:5

title: SoA: one contiguous array per field
row: 32
spot[0]:8:1 spot[1]:8:1 spot[2]:8:1 spot[3]:8:1
strike[0]:8:2 strike[1]:8:2 strike[2]:8:2 strike[3]:8:2
vol[0]:8:4 vol[1]:8:4 vol[2]:8:4 vol[3]:8:4
```

---

## Stretch goals

:::exercise Volatility smile
Generate "market" prices from a made-up smile, σ(K) = 0.2 + 0.1·(ln(K/S))², across 50 strikes. Recover the
implied vols with your solver, and print them as a table (or plot them). How long does it take per strike?
:::

:::exercise Quasi-random numbers
Replace `std::mt19937_64` with a Sobol sequence (write a 1-D Sobol generator, or use the van der Corput sequence
in base 2) plus an inverse normal CDF. Compare the error against path count for pseudo-random and quasi-random
draws. Quasi-Monte Carlo error falls like ~1/n instead of 1/√n.
:::

:::exercise Parallel Monte Carlo
Split the paths across threads with `std::jthread`. Each thread needs its **own** generator. How do you seed them
so the result is reproducible regardless of thread count? (Look up "counter-based RNG" and "skip-ahead".)
:::
