// Module worker that runs Clang/LLD (compiled to WebAssembly) off the main thread.
// The toolchain (~105 MB, ~25 MB compressed) is fetched on the first compile and kept in memory for
// later ones; the browser's HTTP cache makes it fast on later page loads.
//
//   in:  { id, bundle, args, files }        files: { "name.cpp": "source", ... }
//   out: { id, type: "progress", done, total }
//        { id, type: "done", ok, wasm?, output, ms }

let clang = null;

self.onmessage = async ({ data }) => {
  const { id, bundle, args, files } = data;
  const post = (msg, transfer) => self.postMessage({ id, ...msg }, transfer || []);
  let output = "";
  const decoders = [new TextDecoder(), new TextDecoder()];
  const sink = (i) => (bytes) => { if (bytes) output += decoders[i].decode(bytes, { stream: true }); };
  let lastProgress = 0;

  try {
    clang ??= import(bundle);
    const { runClang, Exit } = await clang;
    const t0 = performance.now();
    let result;
    try {
      result = await runClang(args, files, {
        stdout: sink(0),
        stderr: sink(1),
        decodeASCII: false,
        fetchProgress: ({ totalLength, doneLength }) => {
          const now = performance.now();
          if (now - lastProgress > 100 || doneLength >= totalLength) {
            lastProgress = now;
            post({ type: "progress", done: doneLength, total: totalLength });
          }
        },
      });
    } catch (err) {
      if (err instanceof Exit) {
        return post({ type: "done", ok: false, output, ms: Math.round(performance.now() - t0) });
      }
      throw err;
    }
    const ms = Math.round(performance.now() - t0);
    const wasm = result["prog.wasm"];
    if (!(wasm instanceof Uint8Array)) {
      return post({ type: "done", ok: false, output: output + "\nThe linker produced no program.", ms });
    }
    post({ type: "done", ok: true, wasm, output, ms }, [wasm.buffer]);
  } catch (err) {
    clang = null;   // let the next attempt retry the download
    post({ type: "done", ok: false, internal: true, output: `${output}\n${err && err.stack || err}` });
  }
};
