// Classic worker that runs one compiled program (wasm32-wasip1) with a minimal WASI implementation:
// stdout/stderr, clocks, randomness, argv/environ and exit. No filesystem, no network.
// The page terminates this worker on timeout, which is how infinite loops are stopped.
//
//   in:  { wasm: Uint8Array }
//   out: { type: "out", text }  (batched)   then   { type: "exit", code, ms, error? }

const ESUCCESS = 0, EBADF = 8, EINVAL = 28, ENOSYS = 52, ESPIPE = 70;
const MAX_OUTPUT = 20000;

class ProcExit { constructor(code) { this.code = code; } }

self.onmessage = async ({ data }) => {
  let memory = null;
  const view = () => new DataView(memory.buffer);
  const bytes = () => new Uint8Array(memory.buffer);
  const decoder = new TextDecoder();
  let pending = "", total = 0, truncated = false, flushTimer = null;

  const flush = () => {
    clearTimeout(flushTimer); flushTimer = null;
    if (pending) { self.postMessage({ type: "out", text: pending }); pending = ""; }
  };
  const write = (chunk) => {
    if (truncated) return;
    const text = decoder.decode(chunk, { stream: true });
    if (total + text.length > MAX_OUTPUT) {
      pending += text.slice(0, MAX_OUTPUT - total) + "\n… output truncated\n";
      truncated = true;
    } else {
      pending += text;
      total += text.length;
    }
    flushTimer ??= setTimeout(flush, 50);
  };

  const args = ["prog"];
  const enc = new TextEncoder();
  const strings = (list, ptrs, buf) => {
    const dv = view(), mem = bytes();
    for (const s of list) {
      const b = enc.encode(s + "\0");
      dv.setUint32(ptrs, buf, true); ptrs += 4;
      mem.set(b, buf); buf += b.length;
    }
    return ESUCCESS;
  };
  const sizes = (list, countPtr, sizePtr) => {
    const dv = view();
    dv.setUint32(countPtr, list.length, true);
    dv.setUint32(sizePtr, list.reduce((n, s) => n + enc.encode(s).length + 1, 0), true);
    return ESUCCESS;
  };
  const now = (id) => id === 0
    ? BigInt(Date.now()) * 1000000n
    : BigInt(Math.round((performance.timeOrigin + performance.now()) * 1e6));

  const wasi = {
    args_get: (ptrs, buf) => strings(args, ptrs, buf),
    args_sizes_get: (c, s) => sizes(args, c, s),
    environ_get: (ptrs, buf) => strings([], ptrs, buf),
    environ_sizes_get: (c, s) => sizes([], c, s),
    clock_res_get: (id, ptr) => { view().setBigUint64(ptr, 1000n, true); return ESUCCESS; },
    clock_time_get: (id, _precision, ptr) => { view().setBigUint64(ptr, now(id), true); return ESUCCESS; },
    random_get: (ptr, len) => {
      for (let off = 0; off < len; off += 65536) {
        crypto.getRandomValues(new Uint8Array(memory.buffer, ptr + off, Math.min(65536, len - off)));
      }
      return ESUCCESS;
    },
    fd_write: (fd, iovs, iovsLen, nwritten) => {
      if (fd !== 1 && fd !== 2) return EBADF;
      const dv = view();
      let n = 0;
      for (let i = 0; i < iovsLen; i++) {
        const ptr = dv.getUint32(iovs + i * 8, true), len = dv.getUint32(iovs + i * 8 + 4, true);
        write(new Uint8Array(memory.buffer, ptr, len).slice());
        n += len;
      }
      dv.setUint32(nwritten, n, true);
      return ESUCCESS;
    },
    fd_read: (fd, _iovs, _iovsLen, nread) => {   // stdin is always empty
      if (fd !== 0) return EBADF;
      view().setUint32(nread, 0, true);
      return ESUCCESS;
    },
    fd_fdstat_get: (fd, ptr) => {
      if (fd > 2) return EBADF;
      const dv = view();
      dv.setUint8(ptr, 2);              // character device
      dv.setUint16(ptr + 2, 0, true);   // flags
      dv.setBigUint64(ptr + 8, 0n, true);
      dv.setBigUint64(ptr + 16, 0n, true);
      return ESUCCESS;
    },
    fd_fdstat_set_flags: () => ESUCCESS,
    fd_seek: (fd) => (fd > 2 ? EBADF : ESPIPE),
    fd_close: (fd) => (fd > 2 ? EBADF : ESUCCESS),
    fd_sync: () => ESUCCESS,
    fd_prestat_get: () => EBADF,        // no preopened directories
    fd_prestat_dir_name: () => EINVAL,
    sched_yield: () => ESUCCESS,
    proc_exit: (code) => { throw new ProcExit(code); },
  };
  // Anything else (files, sockets, polling) is unsupported.
  const imports = {
    wasi_snapshot_preview1: new Proxy(wasi, { get: (t, k) => t[k] || (() => ENOSYS) }),
  };

  const t0 = performance.now();
  let code = 0, error;
  try {
    const { instance } = await WebAssembly.instantiate(data.wasm, imports);
    memory = instance.exports.memory;
    instance.exports._start();
  } catch (err) {
    if (err instanceof ProcExit) code = err.code;
    else {
      code = 134;
      error = err instanceof WebAssembly.RuntimeError
        ? `Runtime error: ${err.message} (the program trapped, e.g. abort(), a failed assert, or an out-of-bounds access)`
        : `Runtime error: ${err && err.message || err}`;
    }
  }
  const tail = decoder.decode();
  if (tail) write(enc.encode(tail));
  flush();
  self.postMessage({ type: "exit", code, error, ms: Math.round(performance.now() - t0) });
};
