+++
title = "Algorithms, Iterators and Ranges"
minutes = 45
practice_tags = ["stl", "algorithms"]
+++

## Why this chapter matters

Here's a hand-written loop that finds the first price level with enough size to fill an order:

```cpp
int i = 0;
for (; i <= levels.size(); ++i)
    if (levels[i].qty >= needed) break;
```

It has two bugs: `<=` reads one past the end, and `int` vs `size_t` compares signed with unsigned. And a reader
has to trace the loop to work out what it does. The algorithm version has neither problem and says what it means:

```cpp
auto it = std::ranges::find_if(levels, [&](const Level& l) { return l.qty >= needed; });
```

The standard library has about a hundred algorithms: searching, sorting, partitioning, accumulating, transforming,
heap operations. They're correct, well-tested, often faster than hand-written loops (they use `memmove`, SIMD and
careful tuning), and they name the operation. Knowing them well is one of the biggest productivity and correctness
wins in C++.

By the end of this chapter you should be able to:

- explain iterators, iterator categories and half-open ranges;
- use the most important algorithms, and know their complexity;
- use C++20 range algorithms, projections and views;
- recognize when a view pipeline is efficient and when it isn't.

---

## 1. Iterators and half-open ranges

An **iterator** is a generalized pointer: something you can dereference (`*it`) and advance (`++it`). Every
container provides `begin()` and `end()`, and every classic algorithm takes a pair of iterators describing a
**half-open range** `[first, last)`: `first` is included, `last` is one past the end and is never dereferenced.

```cpp
std::vector<std::int64_t> px = {100, 101, 102, 103};
auto first = px.begin(), last = px.end();   // last points one past 103
std::sort(first, last);
std::size_t n = last - first;               // size of the range: no off-by-one arithmetic
```

Half-open ranges make empty ranges trivial (`first == last`), sizes simple (`last - first`), and splitting easy
(`[first, mid)` and `[mid, last)` share no element). That's why the convention is universal.

### Iterator categories

Iterators come in a hierarchy of capabilities, and algorithms state what they need:

| Category | Operations | Containers |
|---|---|---|
| input | read once, `++` | `std::istream_iterator`, network streams |
| forward | read repeatedly, `++` | `forward_list`, `unordered_*` |
| bidirectional | forward plus `--` | `list`, `map`, `set` |
| random access | bidirectional plus `it + n`, `it[n]`, `it2 - it1`, `<` | `deque` |
| contiguous | random access plus elements adjacent in memory | `vector`, `array`, `string`, `span` |

So `std::sort` requires random-access iterators and won't compile on a `std::list` (which has its own
`list::sort`), and `std::lower_bound` works on any forward range but is only O(log n) in *steps* for random-access
ones; on a `std::map`'s iterators it would walk linearly. Use `map::lower_bound` instead.

---

## 2. The algorithms worth knowing

You don't need to memorize all of `<algorithm>` and `<numeric>`, but you should recognize these on sight:

| Task | Algorithm | Complexity |
|---|---|---|
| find first match | `find`, `find_if`, `find_if_not` | O(n) |
| test all/any/none | `all_of`, `any_of`, `none_of` | O(n) |
| count | `count`, `count_if` | O(n) |
| search a **sorted** range | `lower_bound`, `upper_bound`, `equal_range`, `binary_search` | O(log n) |
| sort | `sort` (unstable), `stable_sort` | O(n log n) |
| top k / k-th element | `partial_sort`, `nth_element` | O(n log k), O(n) average |
| split by predicate | `partition`, `stable_partition` | O(n) |
| min / max | `min_element`, `max_element`, `minmax_element` | O(n) |
| sum / fold | `std::accumulate`, `std::reduce`, `std::ranges::fold_left` (C++23) | O(n) |
| prefix sums | `partial_sum`, `inclusive_scan`, `exclusive_scan` | O(n) |
| transform | `transform` | O(n) |
| remove matching (then erase) | `remove_if` + `erase`, or `std::erase_if` | O(n) |
| copy / fill | `copy`, `copy_n`, `fill`, `fill_n` | O(n), often `memmove`/`memset` |
| heap | `make_heap`, `push_heap`, `pop_heap` | O(n), O(log n) |

Some finance examples:

```cpp
// VWAP over a trade tape
double notional = std::transform_reduce(tape.begin(), tape.end(), 0.0, std::plus{},
                                        [](const Trade& t) { return t.price * t.qty; });

// first ask level at or above a price (asks sorted ascending)
auto lvl = std::lower_bound(asks.begin(), asks.end(), px,
                            [](const Level& l, std::int64_t p) { return l.price < p; });

// median latency without sorting everything
auto mid = lat.begin() + lat.size() / 2;
std::nth_element(lat.begin(), mid, lat.end());   // O(n); *mid is now the median

// 10 largest positions by notional, in order
std::partial_sort(pos.begin(), pos.begin() + 10, pos.end(),
                  [](const Position& a, const Position& b) { return a.notional > b.notional; });
```

> [!PERF]
> `nth_element` and `partial_sort` are often overlooked. Computing a p99 latency over a million samples with a
> full `sort` costs O(n log n); `nth_element` does it in O(n), typically 5–10× faster. Chapter 6.4 builds
> streaming histograms that avoid storing the samples at all.

### `accumulate` vs `reduce`

`std::accumulate` folds strictly left to right, so the compiler can't reorder floating-point additions, and the
loop can't be vectorized. `std::reduce` is allowed to reorder and regroup the operations, which permits
vectorization and parallel execution, but gives slightly different floating-point results. For integer sums
they're equivalent. Chapter 14.3 discusses floating-point summation accuracy.

Watch the initial value's type: `std::accumulate(v.begin(), v.end(), 0)` on a vector of `double` sums into an
**`int`**, truncating every partial sum. Write `0.0`.

### Exercises

:::exercise Name the algorithm
Which algorithm does each of these? (a) Is any order's quantity negative? (b) Split orders into marketable ones
first and resting ones after. (c) The position of the first trade with timestamp ≥ t, in a time-sorted tape.
(d) The 95th percentile of a vector of latencies. (e) Cumulative volume at each level from the best price.
:::solution
(a) `std::ranges::any_of(orders, [](auto& o) { return o.qty < 0; })`. (b) `std::partition` (or `stable_partition`
to keep time order within each group). (c) `std::ranges::lower_bound(tape, t, {}, &Trade::ts)`. (d)
`std::nth_element` at index `0.95 * n`. (e) `std::inclusive_scan` (or `std::partial_sum`) over the level
quantities.
:::

---

## 3. C++20 range algorithms

Every classic algorithm has a counterpart in `std::ranges` that takes a whole range instead of an iterator pair,
and adds **projections**:

```cpp
std::ranges::sort(orders, {}, &Order::price);   // sort by price: {} is the default comparator std::ranges::less
auto it = std::ranges::find(orders, 42, &Order::id);
auto [lo, hi] = std::ranges::minmax(prices);
bool sorted = std::ranges::is_sorted(levels, std::greater{}, &Level::price);
```

A **projection** is applied to each element before the comparison or predicate, so you don't need a lambda just to
pick a member. `&Order::price` works because the ranges algorithms call projections with `std::invoke`, which
handles pointers to members.

Range algorithms are also constrained with concepts (Chapter 3.4), so passing a `std::list` to `std::ranges::sort`
gives a short error saying the range isn't random-access, instead of pages of template errors.

> [!TIP]
> Range algorithms return more information than the classic ones. `std::ranges::copy` returns both the end of the
> input and the end of the output, and many return iterators instead of discarding them. Prefer `std::ranges::`
> versions in new code.

---

## 4. Views: lazy, composable ranges

A **view** is a lightweight range that refers to other data and computes elements on demand. Views compose with
`|`:

```cpp
auto large_buys = orders
                | std::views::filter([](const Order& o) { return o.side == Side::Buy && o.qty >= 1000; })
                | std::views::transform(&Order::price);

for (std::int64_t px : large_buys) use(px);   // nothing is computed until here, element by element
```

No intermediate vectors are created. Each element is pulled through the filter and the transform as the loop asks
for it. Useful views:

| View | Produces |
|---|---|
| `views::filter(pred)` | elements satisfying `pred` |
| `views::transform(f)` | `f(element)` |
| `views::take(n)`, `views::drop(n)` | first n / all but first n |
| `views::take_while(pred)`, `views::drop_while(pred)` | prefix while `pred` holds / the rest |
| `views::reverse` | elements in reverse |
| `views::iota(a, b)` | integers `a, a+1, ..., b-1` |
| `views::keys`, `views::values` | first / second of pairs (e.g. from a map) |
| `views::enumerate`, `views::zip`, `views::chunk`, `views::slide`, `views::adjacent` | C++23 |

```cpp
// Rolling 5-tick windows over a price series (C++23)
for (auto window : prices | std::views::slide(5)) {
    double avg = std::reduce(window.begin(), window.end(), 0.0) / 5;
    ...
}
```

### The cost of views

Views are templates, and simple pipelines like `filter | transform` usually compile to the same code as the
hand-written loop. But there are traps:

- **`filter` caches `begin()`**, so a filter view is stateful: iterating a `const` filter view doesn't compile, and
  modifying the underlying data after the first iteration can give stale results.
- **`filter` + `reverse`, or `filter` followed by `take`,** can call the predicate more times than you expect.
- **`transform` calls its function every time an element is dereferenced**, and some views (like `filter` sitting
  after a `transform`) dereference more than once per element. An expensive transform gets computed twice.
- **Deep pipelines compile slowly**, and debug builds are very slow, because nothing is inlined.
- **Views dangle like any other reference** (Chapter 1.4). A view over a temporary vector, stored and used later,
  reads freed memory. The ranges library rejects some of these at compile time (`std::ranges::dangling`), but not
  all.

> [!PERF]
> On a hot path, check the generated code (Chapter 6.3) for any view pipeline more complex than one or two stages.
> When a loop needs to vectorize, a plain loop or a classic algorithm over a contiguous range is the most reliable
> way to get there.

### Exercises

:::exercise Build a pipeline
Using views, produce the first 5 trade sizes (as `std::int64_t`) of sells above price 10,000 from a
`std::vector<Trade> tape`. Collect them into a `std::vector` (C++23 has `std::ranges::to`).
:::solution
```cpp
auto sizes = tape
           | std::views::filter([](const Trade& t) { return t.side == Side::Sell && t.price > 10'000; })
           | std::views::transform(&Trade::qty)
           | std::views::take(5)
           | std::ranges::to<std::vector<std::int64_t>>();
```
Without C++23: `std::vector<std::int64_t> sizes; std::ranges::copy(view, std::back_inserter(sizes));`. The pipeline
stops scanning the tape as soon as it has found 5 matches.
:::

---

## 5. When to write the loop yourself

Algorithms aren't always the best choice:

- **Several things in one pass.** Computing the min, the max, the sum and the count with four algorithm calls
  makes four passes over memory. One hand-written loop makes one. For data larger than the cache, that's up to 4×.
- **Early exit with complex state**, where the lambda would need to capture and mutate several variables.
- **Code that must vectorize in a particular way** (Chapter 9), where you want explicit control.

The rule: reach for an algorithm first; write a loop when the algorithm makes the code less clear or measurably
slower, and then make the loop's intent obvious.

---

## Summary

- Iterators generalize pointers; algorithms work on half-open ranges `[first, last)`.
- Iterator categories (input, forward, bidirectional, random access, contiguous) determine which algorithms apply
  and how fast they are.
- Know the core algorithms, especially `lower_bound`, `nth_element`, `partial_sort`, `partition`,
  `transform_reduce` and `erase_if`.
- `accumulate` is strictly ordered; `reduce` can be vectorized. Watch the initial value's type.
- Prefer `std::ranges::` algorithms: whole-range arguments, projections, better errors.
- Views are lazy and compose with `|`. Simple pipelines are free; complex ones can recompute, cache state, and
  dangle.
- Write a loop when it does several things in one pass or is genuinely clearer.

## Key terms

**Iterator** · **half-open range** · **iterator category** · **contiguous iterator** · **algorithm** ·
**projection** · **range algorithm** · **view** · **lazy evaluation** · **pipeline** · **`std::ranges::dangling`**
