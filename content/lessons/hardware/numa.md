+++
title = "Multicore, NUMA and Topology"
minutes = 35
practice_tags = ["concurrency", "cache"]
+++

## Why this chapter matters

A trading process runs its feed handler on one core and its strategy on another, passing updates through a shared
queue. On a developer's laptop, a message crosses from one thread to the other in about 70 ns. Deployed on a
two-socket production server, it sometimes takes 70 ns and sometimes over 150 ns, depending on which cores the
operating system happens to schedule the threads on. When the strategy's memory was allocated by a thread on the
other socket, every cache miss is also slower.

A modern server isn't one computer with many cores. It's a collection of cores, caches and memory controllers
connected by on-chip and between-chip links, and the cost of communicating depends on **where** two pieces of the
system sit relative to each other. Low-latency systems control this placement explicitly. This chapter gives you the
map; Chapters 10.3 and 10.4 put it to work.

By the end of this chapter you should be able to:

- describe sockets, cores, hardware threads and how caches are shared between them;
- explain how cache lines move between cores, and what that costs;
- explain NUMA: local versus remote memory, and the first-touch policy;
- read a machine's topology with standard tools and place threads and memory deliberately.

---

## 1. Sockets, cores and hardware threads

The layers, from largest to smallest:

- A **socket** (or package) is one physical CPU chip. Servers have one, two, or occasionally more.
- Each socket has many **cores**, each a complete out-of-order execution engine (Chapter 5.1) with private L1 and
  L2 caches.
- Each core may run two **hardware threads** (Intel's Hyper-Threading, generically SMT). The two threads share the
  core's execution units, L1 and L2 caches and branch predictor; the OS sees each as a separate CPU.
- The cores on a socket share an L3 cache and the socket's memory controllers. On AMD EPYC, the socket is further
  divided into chiplets (CCDs/CCXs), each with its own L3, so even within a socket, some cores are "closer" than
  others.

Seeing your machine's layout:

```bash
lscpu                       # sockets, cores per socket, threads per core, cache sizes, NUMA nodes
lscpu -e                    # one line per logical CPU: which core, socket and NUMA node it belongs to
lstopo                      # (hwloc package) a diagram of the whole hierarchy
numactl --hardware          # NUMA nodes, their memory, and the distance matrix
```

The test machine is simple: one socket, 4 cores, no SMT, one shared 6 MB L3, one NUMA node. Production trading
servers are typically one or two sockets with 8–64 cores each.

> [!WARNING]
> Two hardware threads on the same core compete for everything. A latency-critical thread whose sibling is busy can
> run noticeably slower and with more jitter. Low-latency hosts commonly **disable SMT** in the BIOS, or leave the
> sibling of every critical core idle (Chapter 10.4).

---

## 2. Moving cache lines between cores

Each core has its own L1 and L2. When two cores use the same cache line, the hardware keeps their copies consistent
with a **cache coherence protocol** (MESI and its variants, Chapter 10.3). The rule that matters for performance:

> A cache line can be **shared** by many cores for reading, but only **one** core can hold it for writing. When
> another core wants to write it, or read a line someone else has modified, the line must be transferred.

The cost of a transfer depends on distance. Measured on the test machine with two threads passing a value back and
forth through one atomic variable (each write forces the line to move to the other core):

| Pair | Round trip | One-way transfer |
|---|---|---|
| core 0 ↔ core 1 | 140 ns | ~70 ns |
| core 0 ↔ core 2 | 145 ns | ~72 ns |
| core 0 ↔ core 3 | 144 ns | ~72 ns |

On a single-socket desktop all pairs are about the same, since the line travels through the shared L3. Typical
figures on servers:

| Path | Typical one-way cost |
|---|---|
| Two hardware threads of the same core | ~10–20 ns (shared L1/L2) |
| Two cores on the same socket (same L3 / CCX) | ~30–80 ns |
| Two cores on different chiplets of one AMD socket | ~80–120 ns |
| Two cores on different sockets | ~100–200+ ns |

So the placement of two communicating threads can change the latency of every message between them by 2–3×. For an
SPSC queue between a feed thread and a strategy thread (Chapter 11.3), the two threads should be on cores of the
same socket, ideally sharing an L3 slice.

### Exercises

:::exercise Pick the cores
A two-socket server has cores 0–15 on socket 0 and 16–31 on socket 1; the network card is attached to socket 0's PCIe
lanes. Where would you place: the thread that reads packets from the NIC, the strategy thread it sends updates to,
and a logging thread that receives records from both?
:::solution
NIC reader and strategy on socket 0, on two different physical cores (not SMT siblings): the NIC writes packet data
into memory near socket 0, and the two threads exchange cache lines constantly, so they should share an L3. The
logging thread can go on another core of socket 0 if it's light, or on socket 1 if it does heavy work (formatting,
I/O) that would otherwise pollute socket 0's L3; its queue traffic then crosses sockets, but logging isn't on the
latency-critical path.
:::

---

## 3. NUMA: not all memory is equal

In a multi-socket server, each socket has its own memory controllers and its own DIMMs. A core can access any
memory in the machine, but memory attached to its own socket is **local**, and memory attached to another socket is
**remote**: the request has to cross the inter-socket link (Intel UPI, AMD Infinity Fabric). This is
**Non-Uniform Memory Access (NUMA)**.

Typical costs:

| Access | Latency | Bandwidth |
|---|---|---|
| Local DRAM | ~80–100 ns | full |
| Remote DRAM (other socket) | ~1.3–2× local | reduced, and shared with other cross-socket traffic |

`numactl --hardware` prints a **distance matrix** (10 for local, larger numbers for remote) giving the relative
costs.

### First touch

Which node does memory come from? Linux's default policy is **first touch**: a page is allocated on the NUMA node of
the CPU that *first writes to it*, not the one that called `malloc`. That's usually what you want, but it causes a
classic mistake:

```cpp
// main thread, running on socket 1, initializes everything...
Book* book = new Book(...);           // pages allocated on socket 1 when touched
book->prefault();
// ...then starts the strategy thread pinned to socket 0
std::thread strategy([&] { pin_to(3); run(book); });   // every miss on book is a remote access
```

The fix is to allocate and touch (pre-fault) memory **from the thread that will use it**, after pinning it, or to
bind memory explicitly:

```bash
numactl --cpunodebind=0 --membind=0 ./trading_engine     # run on node 0's CPUs, allocate only from node 0
```

```cpp
#include <numa.h>                                          // libnuma
void* p = numa_alloc_onnode(bytes, 0);                     // allocate on node 0
```

> [!PERF]
> Most low-latency trading processes avoid NUMA effects by design: the whole latency-critical path (NIC, threads,
> memory) lives on one socket, and everything is pinned and pre-faulted there at start-up. The other socket runs
> less sensitive work, or nothing.

---

## 4. A placement checklist

For a latency-critical process on a multi-socket server:

1. **Know the topology**: `lscpu -e`, `numactl --hardware`, and which socket the NIC is attached to
   (`cat /sys/class/net/<if>/device/numa_node`).
2. **Choose one socket** for the hot path: the NIC's socket.
3. **Pin each hot thread** to its own physical core on that socket (Chapter 10.4), with no SMT sibling sharing it.
4. **Allocate and pre-fault memory from the thread that uses it**, or bind it to the socket's node.
5. **Keep other processes off** those cores (`isolcpus`, cgroups) and, ideally, off that socket's L3.
6. **Measure core-to-core latency** on the actual hardware with a ping-pong test like the one in this chapter, to
   confirm the placement does what you expect.

---

## Summary

- A server is sockets → cores → hardware threads, with private L1/L2 per core and an L3 shared per socket (or per
  chiplet).
- Cache lines move between cores when one writes what another uses. Measured core-to-core transfer on the test
  machine: ~70 ns. Cross-socket transfers typically cost 2–3× more.
- In multi-socket machines memory is NUMA: remote DRAM is slower than local. Pages are placed by first touch.
- Place communicating threads on the same socket, on separate physical cores; allocate and touch memory from the
  thread that uses it.
- Use `lscpu`, `lstopo` and `numactl` to see the topology, and measure it.

## Key terms

**Socket** · **core** · **hardware thread (SMT)** · **chiplet** · **cache coherence** · **core-to-core latency** ·
**NUMA** · **local / remote memory** · **NUMA node** · **first-touch policy** · **`numactl`** · **thread placement**
