+++
title = "Aliasing, restrict and Strict Aliasing"
minutes = 40
practice_tags = ["ub", "memory"]
+++

## Why this chapter matters

Two functions that count how many bytes in a buffer have their top bit set:

```cpp
void count_into(const std::vector<std::uint8_t>& v, int& count) {
    for (auto x : v) if (x > 127) ++count;
}

int count_local(const std::vector<std::uint8_t>& v) {
    int c = 0;
    for (auto x : v) if (x > 127) ++c;
    return c;
}
```

On the test machine with random bytes, `count_into` takes **3.3 ns per byte** and `count_local` **0.15 ns**: 23×
slower for what looks like the same loop. The reason is one word: **aliasing**. The compiler has to consider that
`count` might refer to memory inside the buffer it's reading, so every `++count` must be written to memory and the
loop can't be vectorized. With a local variable, nothing else can possibly refer to `c`.

Aliasing, two names referring to overlapping memory, is one of the main reasons compilers generate worse code than
you expect. This chapter explains what the compiler is allowed to assume, how to give it more freedom, and the
**strict aliasing rule**, which is both an optimization tool and a common source of undefined behaviour.

By the end of this chapter you should be able to:

- explain why possible aliasing forces the compiler to reload values and blocks vectorization;
- recognize when the compiler handles it with run-time checks, and when it can't;
- remove aliasing problems with local copies, by-value parameters and `__restrict`;
- state the strict aliasing rule and type-pun safely with `memcpy` and `std::bit_cast`.

---

## 1. Why aliasing pessimizes code

When the compiler sees a write through one pointer or reference and a read through another, it must assume they
**may alias** (point to the same memory) unless it can prove otherwise. Consequences:

- Values loaded before a store must be **reloaded** after it, because the store may have changed them.
- Values can't be kept in registers across stores to possibly overlapping memory.
- Loops can't be vectorized or reordered if an iteration's store might feed a later iteration's load.

In `count_into`, `count` is an `int&`, and the buffer is read as `std::uint8_t`. The compiled loop at `-O2` is:

```text
.L51:
    cmp   BYTE PTR [rax], 0
    jns   .L50
    add   DWORD PTR [rsi], 1     ; ++count: a read-modify-write of memory, every time
.L50:
    add   rax, 1
    cmp   rax, rdx
    jne   .L51
```

Because `count` might overlap the buffer, the compiler can neither keep it in a register nor vectorize the loop (as
it does for `count_local`, processing 32 bytes per iteration with AVX2). The scalar loop also branches on random data
(Chapter 5.4). Both costs come from the possibility of aliasing.

Why could an `int` alias `uint8_t` data? Because of the strict aliasing rule's big exception (section 4): `char`,
`unsigned char` and `std::byte` may alias **anything**, and `std::uint8_t` is `unsigned char`. So any store of any type
might modify bytes being read through a `uint8_t*`, and vice versa. Byte buffers are where aliasing problems are most
common, which is unfortunate, since parsers and network code are full of them.

---

## 2. Run-time alias checks

Often the compiler doesn't give up: it **versions** the loop. It emits a check of whether the arrays overlap, then a
fast vectorized loop for the no-overlap case and a safe scalar loop for the other.

```cpp
void add_arrays(double* a, const double* b, const double* c, int n) {
    for (int i = 0; i < n; ++i) a[i] = b[i] + c[i];
}
```

GCC's vectorization report (`-fopt-info-vec`) for this loop says:

```text
optimized: loop vectorized using 32 byte vectors
optimized:  loop versioned for vectorization because of possible aliasing
```

Measured on the test machine, this version and one with `__restrict` (section 3) run at the same speed (0.31 ns per
element at `-O3 -march=native`): the overlap check runs once per call, not per element. Likewise,
`scale(std::vector<double>& v, const double& factor)`, the textbook example of a reference parameter that might alias
the data, measured identical to passing `factor` by value, because GCC hoisted a check.

So run-time checks rescue simple cases. They can't help when:

- **There are too many pointers** or the access pattern is too complex to check cheaply.
- **The written location is a scalar used as an accumulator** (`count_into`): there's no profitable version to switch
  to, so the compiler keeps the scalar memory update.
- **The aliasing involves memory reached through other pointers**: struct members, `this->`, data behind a pointer
  inside an object.
- **The loop is small** or its trip count unknown, so the check doesn't seem worth it.

### Member variables and `this`

A very common case in classes:

```cpp
class Stats {
public:
    void add_all(const double* xs, int n) {
        for (int i = 0; i < n; ++i) { sum_ += xs[i]; count_ += 1; }   // sum_ and count_ are this->sum_...
    }
private:
    double sum_ = 0;
    long   count_ = 0;
};
```

`xs` could point at `this->sum_`, so `sum_` may have to be stored and reloaded each iteration. The fix is to work on
**local copies** and write back once:

```cpp
void add_all(const double* xs, int n) {
    double s = sum_;
    for (int i = 0; i < n; ++i) s += xs[i];
    sum_ = s;
    count_ += n;
}
```

Locals whose address is never taken can't alias anything. This is the single most useful aliasing fix, and it's
portable and standard.

### Exercises

:::exercise Fix the aliasing
Rewrite `count_into` so the compiler can vectorize it, without changing its signature.
:::solution
```cpp
void count_into(const std::vector<std::uint8_t>& v, int& count) {
    int c = 0;
    for (auto x : v) c += (x > 127);
    count += c;
}
```
The loop now updates a local, which can't alias the buffer, and only writes through the reference once at the end.
This version is as fast as `count_local`.
:::

---

## 3. `__restrict`

C99 has the `restrict` keyword: a promise that, for the pointer's lifetime, the memory it accesses isn't accessed
through any other pointer. C++ doesn't have it in the standard, but GCC, Clang and MSVC all support `__restrict`:

```cpp
void add_arrays(double* __restrict a, const double* __restrict b, const double* __restrict c, int n) {
    for (int i = 0; i < n; ++i) a[i] = b[i] + c[i];   // no overlap check needed
}
```

The compiler can now vectorize without versioning, and keep values in registers across stores. It's most useful in
numeric kernels with several array parameters (Chapter 9.4).

> [!WARNING]
> `__restrict` is a promise, not a check. If the pointers do overlap, the behaviour is undefined, and the vectorized
> loop will silently compute wrong answers. Use it only where the non-overlap is guaranteed by construction, and
> document it.

`__restrict` on member functions (`void f() __restrict`) applies to `this`. It also works on references in GCC and
Clang (`double& __restrict`), though it's less widely relied on.

---

## 4. The strict aliasing rule

The C++ standard lets the compiler assume that objects of **different types don't overlap**, with a few exceptions.
Formally, you may only access an object through a glvalue of:

- its own type (with any `const`/`volatile`), or the corresponding signed/unsigned type;
- `char`, `unsigned char` or `std::byte`, which may examine the bytes of any object.

Accessing an object through any other type is undefined behaviour. This is what lets the compiler assume that a
store through a `float*` doesn't change any `int`:

```cpp
int f(int* i, float* x) {
    *i = 1;
    *x = 2.0f;        // can't change *i: different types
    return *i;        // compiled as "return 1"
}
```

The flip side: **type punning through casts is UB.**

```cpp
double px = 101.25;
std::uint64_t bits = *reinterpret_cast<std::uint64_t*>(&px);     // UB: accessing a double through uint64_t

struct Header { std::uint16_t len; std::uint8_t type; };
auto* h = reinterpret_cast<const Header*>(buffer + offset);      // UB (and possibly misaligned, Chapter 7.3)
```

These often "work" until a compiler upgrade or a different optimization level reorders a load past a store that it
assumed couldn't alias, and the program reads stale data. `-fstrict-aliasing` is on at `-O2`; `-Wstrict-aliasing`
catches some cases.

### Type punning correctly

**`std::memcpy`** copies bytes, which is always allowed, and compilers turn a fixed-size `memcpy` into a single
load or register move:

```cpp
std::uint64_t bits;
std::memcpy(&bits, &px, sizeof bits);       // well defined; compiles to one movq
```

**`std::bit_cast`** (C++20) does the same in one expression, and works in `constexpr`:

```cpp
auto bits = std::bit_cast<std::uint64_t>(px);   // requires same size and trivially copyable types
```

For decoding wire messages, read each field with `memcpy` from its offset, or `memcpy` the whole message into a
properly aligned struct. Either compiles to plain loads. Chapter 13.2 builds a decoder this way.

> [!NOTE]
> Some large codebases (the Linux kernel among them) compile with `-fno-strict-aliasing` to make type punning through
> casts safe. That costs some optimization everywhere. In new code, `memcpy` and `bit_cast` give you correctness
> without the global cost.

### Exercises

:::exercise UB or not?
Which of these are undefined behaviour? (a) Reading a `float`'s bytes through an `unsigned char*`. (b) Reading an
`int32_t` through a `uint32_t*`. (c) Reading a `double` through a `std::uint64_t*`. (d) Writing into a
`std::byte` buffer, then reading it as an `int` with `*reinterpret_cast<int*>(buf)`. (e) `std::bit_cast<float>(i)` for
an `int32_t i`.
:::solution
(a) Fine: `unsigned char` may examine any object's bytes. (b) Fine: the signed/unsigned counterpart is allowed.
(c) UB: unrelated types. (d) UB: there's no `int` object in the buffer, just bytes (C++20's implicit object creation
covers some buffer cases, e.g. after `malloc`, but this pattern is still widely flagged as UB and is also an
alignment hazard); use `memcpy`. (e) Fine: that's what `bit_cast` is for.
:::

---

## Summary

- When two names might refer to the same memory, the compiler must reload values after stores and may not vectorize.
- `char`, `unsigned char`, `std::byte` and `std::uint8_t` alias everything, so byte-buffer code suffers most. Measured:
  accumulating into an `int&` while reading `uint8_t` data was 23× slower than using a local.
- Compilers often version loops with a run-time overlap check, which recovers simple array loops; they can't for
  accumulators, member variables or complex access.
- The portable fix is to copy into locals and write back once. `__restrict` promises non-overlap (UB if wrong).
- Strict aliasing: objects may only be accessed through their own type (or signed/unsigned variant) or byte types.
  Type-pun with `memcpy` or `std::bit_cast`, never with `reinterpret_cast`.

## Key terms

**Aliasing** · **may-alias** · **reload** · **loop versioning** · **`__restrict`** · **strict aliasing rule** ·
**type punning** · **`std::bit_cast`** · **`-fno-strict-aliasing`**
