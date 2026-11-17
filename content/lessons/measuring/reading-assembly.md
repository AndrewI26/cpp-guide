+++
title = "Reading Assembly"
minutes = 45
practice_tags = ["toolchain", "inline"]
+++

## Why this chapter matters

You've replaced a loop with a version you expect the compiler to vectorize, and the benchmark shows no change. Did
the compiler vectorize the old one too? Not vectorize the new one? Delete the loop entirely? Inline the function you
thought was a call? You can argue about it, or you can look.

The generated machine code is the ground truth of what your C++ became. You don't need to *write* assembly to do
performance work, but you do need to read enough of it to answer questions like: was this inlined? Is this loop
vectorized? Is that a branch or a `cmov`? Why is there a reload from memory inside my loop? All the listings in
this chapter are real output from GCC 15 on the test machine.

By the end of this chapter you should be able to:

- produce assembly for a function with GCC/Clang flags or Compiler Explorer;
- read basic x86-64 instructions, registers, addressing modes and calling-convention patterns;
- recognize inlining, vectorization, conditional moves, and strength reduction in compiler output;
- spot common performance problems: unexpected calls, reloads, spills and scalar loops.

---

## 1. Getting the assembly

**Compiler Explorer** (godbolt.org) is the fastest way: paste code, pick a compiler and flags, and see the assembly
with each source line colour-matched to its instructions. Use it constantly; it's the single most useful tool in
this part of the book.

Locally:

```bash
g++ -std=c++20 -O2 -S -masm=intel -fno-asynchronous-unwind-tables -o - file.cpp | c++filt   # assembly to stdout
objdump -d -M intel --no-show-raw-insn -C ./binary | less                                   # disassemble a binary
perf annotate                                                                                # assembly with sample counts
```

- `-masm=intel` gives Intel syntax (`mov dst, src`), which this book uses. The default AT&T syntax reverses operand
  order (`mov src, dst`) and adds `%` and `$` sigils.
- `-fno-asynchronous-unwind-tables` removes the `.cfi_*` directives, which are unwinding metadata, not code.
- `c++filt` (or `objdump -C`) demangles names like `_Z8notionalll` into `notional(long, long)`.

---

## 2. Enough x86-64 to get by

### Registers

| Registers | Use |
|---|---|
| `rax rbx rcx rdx rsi rdi rbp rsp r8`–`r15` | 64-bit general purpose. `eax` is the low 32 bits of `rax`, `ax` the low 16, `al` the low 8. Writing a 32-bit register zeroes the upper half. |
| `rsp` | stack pointer |
| `rip` | instruction pointer (`[rip + x]` addresses globals and constants) |
| `xmm0`–`xmm15` | 128-bit vector registers; scalar `float`/`double` live here too |
| `ymm0`–`ymm15` | 256-bit (AVX/AVX2); `zmm` are 512-bit (AVX-512) |

Calling convention (Chapter 2.1): integer arguments in `rdi, rsi, rdx, rcx, r8, r9`; floating-point in `xmm0`–`xmm7`;
return value in `rax` or `xmm0`.

### Instructions you'll see constantly

| Instruction | Meaning |
|---|---|
| `mov dst, src` | copy (register, memory or immediate) |
| `lea dst, [addr]` | compute an address *without* loading; often used for arithmetic like `a + b*4 + 8` |
| `add sub imul and or xor` | arithmetic and logic; `xor eax, eax` is the idiom for zeroing a register |
| `cmp a, b` / `test a, b` | set flags from `a - b` / `a & b` |
| `jmp`, `je jne jl jg jle jge jb ja` | unconditional / conditional jumps (the letters name the flag condition) |
| `cmovCC dst, src` | conditional move: no branch |
| `setCC r8` | set a byte to 0 or 1 from a condition |
| `call` / `ret` | call a function / return |
| `push` / `pop` | stack operations, mostly in prologues and epilogues |
| `movsd addsd mulsd divsd` | scalar `double` operations (`ss` suffix for `float`) |
| `movdqu paddq` / `vmovupd vaddpd` / `vfmadd231pd` | SSE / AVX packed (vector) operations |

Memory operands are written `QWORD PTR [base + index*scale + offset]`, where `QWORD` is 8 bytes, `DWORD` 4, `BYTE` 1,
`XMMWORD` 16 and `YMMWORD` 32.

### A first function

```cpp
std::int64_t notional(std::int64_t px, std::int64_t qty) { return px * qty; }
```

At `-O2`:

```text
_Z8notionalll:
    mov   rax, rdi        ; px arrives in rdi; return value goes in rax
    imul  rax, rsi        ; multiply by qty (in rsi)
    ret
```

At `-O0`, the same function stores both arguments to the stack and reloads them:

```text
_Z8notionalll:
    push  rbp
    mov   rbp, rsp
    mov   QWORD PTR -8[rbp], rdi     ; spill px to the stack
    mov   QWORD PTR -16[rbp], rsi    ; spill qty
    mov   rax, QWORD PTR -8[rbp]     ; reload px
    imul  rax, QWORD PTR -16[rbp]    ; multiply by qty from memory
    pop   rbp
    ret
```

That's why `-O0` performance tells you nothing (Chapter 1.2).

### Exercises

:::exercise Read it
What C++ function produced this, given that the function takes two `int64_t` arguments?

```text
    cmp    rdi, rsi
    mov    rax, rsi
    cmovge rax, rdi
    ret
```
:::solution
`std::int64_t best(std::int64_t a, std::int64_t b) { return a > b ? a : b; }` (or `std::max(a, b)`). It compares
`a` (`rdi`) with `b` (`rsi`), sets the result to `b`, and conditionally replaces it with `a` if `a >= b`. Using
`cmovge` rather than `cmovg` is fine, since when they're equal both choices give the same value. There's no branch,
so it can't mispredict (Chapter 5.4).
:::

---

## 3. Recognizing what the optimizer did

### Strength reduction

Expensive operations get replaced by cheaper equivalents. Division by a constant becomes a multiplication by a
"magic" reciprocal and shifts:

```cpp
std::int64_t div10(std::int64_t x) { return x / 10; }
```

```text
_Z5div10l:
    movabs rax, 7378697629483820647   ; ≈ 2^66 / 10
    imul   rdi                        ; 128-bit product in rdx:rax
    sar    rdi, 63                    ; sign of x (0 or -1)
    sar    rdx, 2                     ; high half >> 2
    mov    rax, rdx
    sub    rax, rdi                   ; correct rounding toward zero for negative x
    ret
```

No `idiv` (35–88 cycles) in sight: a multiply and a few shifts, about 5 cycles. This is why dividing by a
compile-time constant is cheap and dividing by a run-time variable isn't (Chapter 5.1 measured 2.2 ns versus 11.6 ns
in a dependency chain). Likewise, `(bid + ask) / 2.0` compiles to a multiplication by `0.5`.

### Loops: scalar vs vectorized

```cpp
std::int64_t sum(const std::int64_t* a, int n) {
    std::int64_t s = 0;
    for (int i = 0; i < n; ++i) s += a[i];
    return s;
}
```

GCC 15 at `-O2` emits a plain scalar loop: one element per iteration.

```text
.L6:
    add   rax, QWORD PTR [rdi]   ; s += *p
    add   rdi, 8                 ; ++p
    cmp   rdi, rdx               ; p != end?
    jne   .L6
```

The giveaway for a loop is a backward jump (`jne .L6` to a label above it). The compiler has also turned `a[i]` with
an index into a pointer that's incremented, which is a common transformation.

At `-O3 -march=native` (AVX2 on the test machine), the loop is vectorized:

```text
.L7:
    vpaddq  ymm0, ymm0, YMMWORD PTR [rax]   ; add 4 int64s at once
    add     rax, 32                          ; advance 32 bytes = 4 elements
    cmp     rdx, rax
    jne     .L7
    vextracti128 xmm1, ymm0, 0x1             ; after the loop: add the 4 lanes together
    vpaddq  xmm0, xmm1, xmm0
    ...                                      ; then a scalar tail for the last n % 4 elements
```

Signs of vectorization: `ymm`/`xmm` registers with **packed** instructions (`p...` integer ops, `...pd`/`...ps`
floating-point), a pointer advancing by 16, 32 or 64 bytes per iteration, a horizontal reduction after the loop,
and a scalar **remainder loop**. Chapter 9.1 covers when and why the compiler vectorizes; note that GCC 12+ does
vectorize some loops at `-O2`, but with a cheaper cost model than `-O3`.

### Inlining

If a function was inlined, there's no `call` to it: its instructions appear inside the caller. Searching the caller
for `call` instructions tells you immediately which calls survived. A `call` to something unexpected
(`operator new`, `memcpy`, `__cxa_throw`, a `std::function` invoker) in a hot loop is worth investigating.

---

## 4. Performance red flags

When reading the assembly of a hot loop, look for:

| You see | Likely cause | Chapter |
|---|---|---|
| `call` inside the loop | a function that wasn't inlined (different TU, too big, virtual, through a pointer) | 2.1, 8.1 |
| loads from the same address every iteration | the compiler can't prove the value doesn't change: aliasing | 8.2 |
| stores and reloads to `[rsp + ...]` | **register spills**: too many live values | 9.2 |
| `div` / `idiv` | division by a run-time value | 5.1 |
| scalar `addsd`/`mulsd` in a loop over an array | not vectorized | 9.1 |
| `cvtsi2sd` / `cvttsd2si` in the loop | int ↔ double conversions you may not have intended | 14.1 |
| conditional jumps on data-dependent values | possible mispredicts | 5.4 |
| `lock` prefix (`lock xadd`, `lock cmpxchg`) | an atomic operation, e.g. a `shared_ptr` copy | 4.6, 11.1 |

A typical example of the aliasing red flag:

```cpp
void scale(std::vector<double>& v, const double& factor) {
    for (double& x : v) x *= factor;
}
```

Because `factor` is a reference that *might* point into `v`, the compiler reloads it from memory every iteration and
can't vectorize freely. Passing `factor` by value removes the reload. Chapter 8.2 explains aliasing in full.

### Exercises

:::exercise Vectorized or not?
This is the hot loop of a function that counts `'B'` bytes in a `std::vector<char>`, compiled at `-O2`. Is it
vectorized? How many bytes does each iteration process?

```text
.L13:
    movdqu   xmm0, XMMWORD PTR [rcx]
    add      rcx, 16
    pcmpeqb  xmm0, xmm5
    pand     xmm0, xmm6
    ...      (unpacking and paddd instructions)
    cmp      rcx, rax
    jne      .L13
```
:::solution
Yes: it loads 16 bytes at once into `xmm0` (`movdqu`), compares all 16 against a register full of `'B'` bytes with
`pcmpeqb`, masks the results to 0 or 1, then widens and adds them into 32-bit counters. The pointer advances by 16,
so each iteration processes **16 bytes**. This is real GCC 15 `-O2` output for
`for (char s : sides) c += (s == 'B');`, so GCC's `-O2` cost model did vectorize this loop even though it left the
`int64_t` sum above scalar.
:::

---

## Summary

- Use Compiler Explorer, `g++ -S -masm=intel`, `objdump -d -C` and `perf annotate` to see generated code.
- Know the basics: general-purpose and vector registers, the calling convention, `mov`/`lea`/arithmetic,
  `cmp`/`jcc`/`cmov`, and memory operand syntax.
- Recognize optimizer work: strength reduction (division by constants), pointer-increment loops, vectorized loops
  (packed ops on `xmm`/`ymm`, reductions, remainder loops), and inlining (no `call`).
- Red flags in hot loops: calls, repeated loads (aliasing), stack spills, divisions, scalar FP math, `lock` prefixes.
- Never judge performance from `-O0` assembly.

## Key terms

**Intel / AT&T syntax** · **register** · **addressing mode** · **`lea`** · **conditional move** ·
**strength reduction** · **vectorized loop** · **remainder loop** · **horizontal reduction** · **register spill** ·
**Compiler Explorer**
