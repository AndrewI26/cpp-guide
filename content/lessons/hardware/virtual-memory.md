+++
title = "Virtual Memory, TLBs and Huge Pages"
minutes = 40
practice_tags = ["memory", "cache"]
+++

## Why this chapter matters

A strategy allocates a 1 GB arena for its order history at start-up. The first few minutes of trading show
latency spikes of tens of microseconds that later disappear. The cause: the arena's memory wasn't really allocated
when `mmap` returned. Each 4 KB page was only provided by the operating system the first time it was touched,
through a **page fault** costing a couple of microseconds, and those first touches happened during trading.

Later in the day, random lookups into that same arena take about 28 ns each on the test machine. Backing it with
**huge pages** instead brings that down to about 11 ns, with no change to the code that does the lookups.

Every address your program uses is a *virtual* address that the hardware translates to a physical one. That
translation is usually invisible, but it has costs: page faults when memory is first touched, and **TLB misses** when
a program touches memory spread over many pages. This chapter explains the mechanism and the standard low-latency
techniques for controlling it.

By the end of this chapter you should be able to:

- explain virtual-to-physical translation, page tables and the TLB;
- estimate when TLB misses will matter, from the number of pages a working set spans;
- explain minor and major page faults, and pre-fault memory before the hot path runs;
- use transparent and explicit huge pages, and know their trade-offs;
- lock memory to prevent it being swapped out.

---

## 1. Virtual memory and page tables

Each process sees its own **virtual address space**: a flat range of addresses, most of them unused. The operating
system maps the parts in use to physical memory in fixed-size **pages**, 4 KB by default on x86-64.

The mapping lives in a **page table**, a four-level tree (five on the newest CPUs) maintained by the kernel. To
translate an address, the hardware **page walker** uses successive groups of address bits to index each level:

```text
virtual address (48 bits used):
| PML4 index (9) | PDPT index (9) | PD index (9) | PT index (9) | offset in page (12) |
       ↓                ↓               ↓              ↓
   level 4 table →  level 3 table → level 2 table → level 1 table → 4 KB physical page
```

A full walk is four dependent memory reads. If the page-table entries are cached, that's maybe 20–30 cycles; if not,
it can be several cache misses, hundreds of cycles, *before* the actual data access can even start.

This indirection is what gives each process its own isolated memory, lets the kernel share libraries between
processes, map files into memory, and hand out memory lazily. It's also why two processes can both use address
`0x7fff...` without conflict.

---

## 2. The TLB

Walking page tables on every access would be ruinous, so the CPU caches recent translations in the **Translation
Lookaside Buffer (TLB)**. On Skylake:

| TLB | Entries | Coverage with 4 KB pages | Coverage with 2 MB pages |
|---|---|---|---|
| L1 data TLB | 64 (4 KB), 32 (2 MB) | 256 KB | 64 MB |
| L2 shared TLB (STLB) | 1,536 | 6 MB | 3 GB |

**TLB reach** is entries × page size: how much memory can be accessed without a TLB miss. With 4 KB pages, anything
beyond about 6 MB of randomly accessed memory misses in the STLB regularly, and every miss costs a page walk on top
of the cache miss for the data itself.

That's what happened in the 512 MB pointer chase in Chapter 5.2: 118 ns per load instead of 101 ns at 64 MB. The
extra is page-table walking.

Measured on the test machine, random independent 8-byte reads over a 1 GB buffer:

| Page size | ns per read |
|---|---|
| 4 KB | 28.5 |
| 2 MB (transparent huge pages) | 10.7 |

The data accesses are identical. With 4 KB pages, 1 GB is 262,144 pages, far beyond the TLB's reach, so nearly every
read also needs a page walk, and the page-table entries themselves (8 bytes per 4 KB page, 2 MB of them) compete
for cache. With 2 MB pages, the same gigabyte is 512 pages, which fits in the STLB entirely. The reads got **2.7×
faster**.

> [!NOTE]
> Sequential access suffers much less: one TLB miss per 4 KB page is amortized over 64 cache lines, and the
> walker can run ahead. TLB costs dominate for **random** access over **large** working sets: hash tables, big order
> books indexed by ID, lookup tables, graph-like structures.

### Exercises

:::exercise TLB reach
A hash table of 50 million 16-byte slots is accessed at random. How many 4 KB pages does it span, and how many 2 MB
pages? Which fits in Skylake's STLB?
:::solution
50 M × 16 B = 800 MB. That's 204,800 pages of 4 KB, far beyond 1,536 STLB entries, so nearly every lookup misses
the TLB. With 2 MB pages it's 400 pages, which fits easily in the STLB (and the 32-entry L1 TLB covers 64 MB of it).
Each lookup still misses the data cache, but no longer pays for a page walk too.
:::

---

## 3. Page faults and pre-faulting

When you `mmap` anonymous memory, or `malloc` a large block (glibc uses `mmap` above 128 KB by default), the kernel
just records that the range is valid. No physical memory is assigned yet. The first access to each page triggers a
**page fault**: the CPU traps into the kernel, which allocates a physical page, **zeroes it**, installs the mapping,
and returns.

- A **minor fault** (memory is available, just not mapped yet) costs roughly 1–3 µs on the test machine: touching
  every 4 KB page of a fresh 1 GB region took 677 ms, about 2.6 µs per page including zeroing.
- A **major fault** (the page must be read from disk, e.g. a memory-mapped file or swapped-out memory) costs
  hundreds of microseconds to milliseconds.

Either is catastrophic on a hot path where the budget is a few microseconds. The cure is to make sure every page the
hot path might touch has been faulted in **before** trading starts:

```cpp
void prefault(void* p, std::size_t bytes) {
    auto* c = static_cast<volatile char*>(p);
    for (std::size_t i = 0; i < bytes; i += 4096) c[i] = 0;   // write: read faults may map a shared zero page
}
```

Or ask the kernel to do it at allocation time:

```cpp
void* p = mmap(nullptr, bytes, PROT_READ | PROT_WRITE,
               MAP_PRIVATE | MAP_ANONYMOUS | MAP_POPULATE, -1, 0);
```

> [!WARNING]
> Pre-faulting by *reading* isn't enough for anonymous memory: Linux maps every untouched page to a single shared
> zero page on a read fault, and the first *write* then faults again to get a private copy. Always write, or use
> `MAP_POPULATE`.

This also applies to stacks (touch the stack depth your hot threads will use), to containers (`reserve` and then
touch), and to memory-mapped files of reference data (read them fully at start-up, or `MAP_POPULATE` them).

### Locking memory

Even pre-faulted memory can be taken away: under memory pressure the kernel may swap pages out, and the next access
is a major fault. Low-latency processes lock their memory:

```cpp
mlockall(MCL_CURRENT | MCL_FUTURE);   // pin all current and future pages in RAM (needs CAP_IPC_LOCK or ulimit -l)
```

`MCL_FUTURE` also makes future allocations fault in eagerly. Production trading hosts typically also disable swap
entirely.

### Exercises

:::exercise Where do faults hide?
List three places in a trading process where page faults can occur during trading even if all large buffers were
pre-faulted at start-up.
:::solution
(1) A `std::vector` or other container growing past its capacity: the new block comes from fresh, untouched memory
(especially for large blocks, which glibc `mmap`s). (2) A thread's stack growing deeper than it ever has before,
for example on a rare code path with large local arrays. (3) Code pages: the first execution of a rarely used
function in the binary or a shared library faults its page in from the page cache. Others: memory-mapped log files
being extended, `malloc` arenas for a newly created thread, and copy-on-write after `fork`.
:::

---

## 4. Huge pages

x86-64 supports 2 MB and 1 GB pages in addition to 4 KB. A huge page is one TLB entry covering 512 (or 262,144)
times as much memory, and one less level of page walk. There are two ways to get them on Linux.

**Transparent Huge Pages (THP).** The kernel automatically backs suitably aligned anonymous memory with 2 MB pages.
The system setting is in `/sys/kernel/mm/transparent_hugepage/enabled`: `always`, `madvise` (only regions that ask)
or `never`. With `madvise`, request them per region:

```cpp
void* p = std::aligned_alloc(2 << 20, bytes);   // 2 MB aligned
madvise(p, bytes, MADV_HUGEPAGE);
```

The 2.7× improvement measured above used exactly this. THP's drawbacks: the kernel may need to **compact** memory to
find contiguous 2 MB regions, and the background `khugepaged` thread can cause latency hiccups when it merges pages.
Many low-latency deployments set THP to `madvise` (or `never`) and use explicit huge pages for the memory that
matters.

**Explicit huge pages (hugetlbfs).** Reserve a pool of huge pages at boot (`hugepages=N` on the kernel command line,
or `/proc/sys/vm/nr_hugepages`), then map them explicitly:

```cpp
void* p = mmap(nullptr, bytes, PROT_READ | PROT_WRITE,
               MAP_PRIVATE | MAP_ANONYMOUS | MAP_HUGETLB, -1, 0);   // fails if the pool is exhausted
```

These pages are reserved up front, never swapped, never compacted or split, and their availability is predictable.
Kernel-bypass networking libraries (DPDK, Chapter 12.3) require them for packet buffers.

| | 4 KB pages | THP (2 MB) | hugetlbfs (2 MB / 1 GB) |
|---|---|---|---|
| Setup | none | kernel setting + `madvise` | reserve pool at boot, `MAP_HUGETLB` |
| TLB reach (STLB) | ~6 MB | ~3 GB | ~3 GB / more with 1 GB pages |
| Fault cost | small, many faults | larger (zero 2 MB), few faults | at reservation; predictable |
| Latency hiccups | none from paging | possible (compaction, khugepaged) | none |
| Memory waste | minimal | up to 2 MB per region | up to a page per region |

> [!PERF]
> The usual recipe for a latency-critical process: allocate all large hot structures (books, order pools, hash
> tables, ring buffers) once at start-up from huge pages, write to every page to pre-fault it, `mlockall`, and never
> return memory to the OS during the session. Chapter 7.5's arenas and pools are built on top of exactly this.

---

## Summary

- Every address is virtual. The page table (a 4-level tree) maps 4 KB pages to physical memory; the TLB caches
  translations.
- TLB reach on Skylake is ~6 MB with 4 KB pages. Random access over larger working sets pays a page walk on top of
  each cache miss.
- Huge pages (2 MB) extend reach to gigabytes: random reads over 1 GB were 2.7× faster on the test machine.
- Fresh memory is mapped lazily: the first write to each page is a ~2–3 µs minor fault. Pre-fault (by writing, or
  `MAP_POPULATE`) before trading, and `mlockall` to prevent swapping.
- THP is convenient but can cause hiccups; hugetlbfs gives predictable, reserved huge pages.

## Key terms

**Virtual address** · **page** · **page table** · **page walk** · **TLB** · **TLB reach** · **page fault (minor /
major)** · **zero page** · **pre-faulting** · **`MAP_POPULATE`** · **`mlockall`** · **huge page** ·
**transparent huge pages** · **hugetlbfs**
