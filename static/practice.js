// Practice list filtering and the problem-page editor / in-browser test runner.
(function () {
  const store = window.cppGuideStore;
  const solved = new Set(store.get("solved", []));
  const saveSolved = () => store.set("solved", [...solved]);
  const escapeHtml = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  // ------------------------------------------------------------ index page
  const dataEl = document.getElementById("problem-data");
  if (dataEl) {
    const problems = JSON.parse(dataEl.textContent);
    const params = new URLSearchParams(location.search);
    const state = {
      q: params.get("q") || "",
      difficulty: params.get("difficulty") || "",
      status: params.get("status") || "",
      tags: new Set(params.getAll("tag")),
    };
    const rows = document.getElementById("problem-rows");
    const search = document.getElementById("search");
    const statusSel = document.getElementById("status-filter");
    search.value = state.q;
    statusSel.value = state.status;

    function matches(p) {
      if (state.difficulty && p.difficulty !== state.difficulty) return false;
      if (state.status === "solved" && !solved.has(p.slug)) return false;
      if (state.status === "unsolved" && solved.has(p.slug)) return false;
      for (const t of state.tags) if (!p.tags.includes(t)) return false;
      if (state.q) {
        const hay = `${p.num} ${p.title} ${p.tags.join(" ")}`.toLowerCase();
        if (!state.q.toLowerCase().split(/\s+/).every((w) => hay.includes(w))) return false;
      }
      return true;
    }

    function syncUrl() {
      const u = new URLSearchParams();
      if (state.q) u.set("q", state.q);
      if (state.difficulty) u.set("difficulty", state.difficulty);
      if (state.status) u.set("status", state.status);
      state.tags.forEach((t) => u.append("tag", t));
      const qs = u.toString();
      history.replaceState(null, "", qs ? `?${qs}` : location.pathname);
    }

    function render() {
      const visible = problems.filter(matches);
      rows.innerHTML = visible.map((p) => `
        <tr data-url="${p.url}">
          <td><span class="status-dot ${solved.has(p.slug) ? "solved" : ""}">${solved.has(p.slug) ? "✓" : ""}</span></td>
          <td class="num-cell">${p.num}</td>
          <td><a href="${p.url}">${escapeHtml(p.title)}</a></td>
          <td><div class="tags-cell">${p.tags.map((t) => `<span class="mini-tag">${escapeHtml(t)}</span>`).join("")}</div></td>
          <td><span class="badge badge-${p.difficulty}">${p.difficulty[0].toUpperCase() + p.difficulty.slice(1)}</span></td>
        </tr>`).join("");
      document.getElementById("empty-state").hidden = visible.length > 0;
      document.getElementById("stat-solved").textContent = problems.filter((p) => solved.has(p.slug)).length;
      document.querySelectorAll("#difficulty-filter button").forEach((b) =>
        b.classList.toggle("active", b.dataset.difficulty === state.difficulty));
      document.querySelectorAll("#tag-filter .chip").forEach((c) =>
        c.classList.toggle("active", state.tags.has(c.dataset.tag)));
      syncUrl();
    }

    rows.addEventListener("click", (ev) => {
      const tr = ev.target.closest("tr[data-url]");
      if (tr && !ev.target.closest("a")) location.href = tr.dataset.url;
    });
    search.addEventListener("input", () => { state.q = search.value.trim(); render(); });
    statusSel.addEventListener("change", () => { state.status = statusSel.value; render(); });
    document.querySelectorAll("#difficulty-filter button").forEach((b) =>
      b.addEventListener("click", () => { state.difficulty = b.dataset.difficulty; render(); }));
    document.querySelectorAll("#tag-filter .chip").forEach((c) =>
      c.addEventListener("click", () => {
        const t = c.dataset.tag;
        state.tags.has(t) ? state.tags.delete(t) : state.tags.add(t);
        render();
      }));
    document.getElementById("random-problem").addEventListener("click", () => {
      let pool = problems.filter((p) => matches(p) && !solved.has(p.slug));
      if (!pool.length) pool = problems.filter((p) => !solved.has(p.slug));
      if (pool.length) location.href = pool[Math.floor(Math.random() * pool.length)].url;
    });
    render();
  }

  // ---------------------------------------------------------- problem page
  const problemEl = document.querySelector(".problem[data-slug]");
  if (problemEl) {
    const slug = problemEl.dataset.slug;
    const cfg = JSON.parse(document.getElementById("problem-config").textContent);
    const hl = window.cppHighlight;
    const editor = document.getElementById("editor");
    const editorHl = document.getElementById("editor-hl");
    const output = document.getElementById("output");
    const status = document.getElementById("run-status");
    const runBtn = document.getElementById("run-code");
    const solvedBtn = document.getElementById("toggle-solved");
    const flag = document.getElementById("solved-flag");
    const starter = cfg.starter;
    const codeKey = `code:${slug}`;
    const RUN_TIMEOUT_MS = 10000;

    const saved = store.get(codeKey, null);
    if (saved !== null) editor.value = saved;

    // ----- syntax highlighting: a <pre> under a transparent <textarea>
    function paintCode() {
      // trailing newline keeps the last line's height when the text ends in "\n"
      editorHl.innerHTML = hl.cpp(editor.value) + "\n";
      syncScroll();
    }
    function syncScroll() {
      editorHl.parentElement.scrollTop = editor.scrollTop;
      editorHl.parentElement.scrollLeft = editor.scrollLeft;
    }
    editor.addEventListener("scroll", syncScroll);
    paintCode();

    // ----- compiler availability
    const hasWasm = cfg.wasm.enabled && typeof Worker !== "undefined" && typeof WebAssembly !== "undefined";
    const clangVersion = cfg.wasm.version.split("-")[0];
    if (hasWasm) {
      output.textContent = `Press “Run tests” to compile with Clang ${clangVersion} (-std=c++20 -O2) right here in your browser.\n` +
        "The first run downloads the compiler (~25 MB); later runs reuse it. Nothing is sent to a server.";
    } else {
      output.textContent = cfg.wasm.enabled
        ? "Your browser doesn't support WebAssembly workers, so tests can't run here. Try a current Chrome, Firefox or Safari."
        : "The in-browser compiler isn't installed on this site, so tests can't run.";
      runBtn.disabled = true;
    }

    function paintSolved() {
      const s = solved.has(slug);
      flag.hidden = !s;
      solvedBtn.textContent = s ? "✓ Solved" : "Mark solved";
      solvedBtn.classList.toggle("is-solved", s);
    }
    paintSolved();

    let saveTimer;
    editor.addEventListener("input", () => {
      paintCode();
      clearTimeout(saveTimer);
      saveTimer = setTimeout(() => store.set(codeKey, editor.value), 300);
    });

    editor.addEventListener("keydown", (ev) => {
      if (ev.key === "Tab") {
        ev.preventDefault();
        const { selectionStart: s, selectionEnd: e, value } = editor;
        if (s === e && !ev.shiftKey) {
          editor.setRangeText("    ", s, e, "end");
        } else {
          // indent / outdent the selected lines
          const lineStart = value.lastIndexOf("\n", s - 1) + 1;
          const block = value.slice(lineStart, e);
          const changed = ev.shiftKey ? block.replace(/^ {1,4}/gm, "") : block.replace(/^/gm, "    ");
          editor.setRangeText(changed, lineStart, e, "select");
        }
        editor.dispatchEvent(new Event("input"));
      } else if (ev.key === "Enter" && !ev.ctrlKey && !ev.metaKey) {
        // keep indentation of the current line
        ev.preventDefault();
        const s = editor.selectionStart;
        const lineStart = editor.value.lastIndexOf("\n", s - 1) + 1;
        const indent = editor.value.slice(lineStart, s).match(/^\s*/)[0];
        const extra = editor.value[s - 1] === "{" ? "    " : "";
        editor.setRangeText("\n" + indent + extra, s, editor.selectionEnd, "end");
        editor.dispatchEvent(new Event("input"));
      } else if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) {
        ev.preventDefault();
        run();
      }
    });

    document.getElementById("reset-code").addEventListener("click", () => {
      if (editor.value !== starter && !confirm("Replace your code with the starter code?")) return;
      editor.value = starter;
      store.set(codeKey, starter);
      paintCode();
    });

    solvedBtn.addEventListener("click", () => {
      solved.has(slug) ? solved.delete(slug) : solved.add(slug);
      saveSolved();
      paintSolved();
    });

    function setOutput(text) {
      output.innerHTML = hl.output(text);
      output.scrollTop = output.scrollHeight;
    }
    function finish(ok, label, text) {
      status.className = `run-status ${ok ? "pass" : "fail"}`;
      status.textContent = label;
      setOutput(text);
      if (ok) { solved.add(slug); saveSolved(); paintSolved(); }
    }

    // ----- in-browser: Clang (WASM) in one worker, the program in a throwaway worker
    let compiler = null, judgeHpp = null, nextId = 0;
    function compile(files, onProgress) {
      compiler ??= new Worker(cfg.wasm.worker, { type: "module" });
      const id = ++nextId;
      return new Promise((resolve) => {
        const onMsg = ({ data }) => {
          if (data.id !== id) return;
          if (data.type === "progress") return onProgress(data);
          compiler.removeEventListener("message", onMsg);
          resolve(data);
        };
        compiler.addEventListener("message", onMsg);
        compiler.onerror = (ev) => {
          compiler.removeEventListener("message", onMsg);
          compiler.terminate(); compiler = null;
          resolve({ ok: false, internal: true, output: `Couldn't start the compiler: ${ev.message || "worker error"}` });
        };
        compiler.postMessage({
          id, files, bundle: new URL(cfg.wasm.bundle, location.href).href,
          // The WASM sysroot's libc++/libc++abi are built without exceptions, so compile without
          // them too: library code then aborts instead of throwing (otherwise __cxa_throw is undefined).
          args: ["clang++", "-std=c++20", "-O2", "-fno-exceptions", "-Wall", "-Wextra", "-fno-color-diagnostics",
                 "-Wl,-z,stack-size=8388608", "main.cpp", "-o", "prog.wasm"],
        });
      });
    }
    function execute(wasm, onOut) {
      return new Promise((resolve) => {
        const w = new Worker(cfg.wasm.runner);
        const timer = setTimeout(() => {
          w.terminate();
          resolve({ code: -1, timedOut: true });
        }, RUN_TIMEOUT_MS);
        w.onmessage = ({ data }) => {
          if (data.type === "out") return onOut(data.text);
          clearTimeout(timer); w.terminate(); resolve(data);
        };
        w.onerror = (ev) => {
          clearTimeout(timer); w.terminate();
          resolve({ code: -1, error: `Runtime error: ${ev.message || "worker error"}` });
        };
        w.postMessage({ wasm }, [wasm.buffer]);
      });
    }
    const UNSUPPORTED = /exceptions disabled|cannot use '(throw|try)'|__cxa_|std::thread|<thread>|pthread_create/;

    async function runInBrowser() {
      status.className = "run-status";
      status.textContent = compiler ? "Compiling…" : "Loading compiler…";
      if (judgeHpp === null) {
        const res = await fetch(cfg.wasm.judge);
        if (!res.ok) throw new Error(`couldn't load judge.hpp (HTTP ${res.status})`);
        judgeHpp = await res.text();
      }
      const files = {
        "solution.hpp": editor.value,
        "judge.hpp": judgeHpp,
        "main.cpp": '#include "solution.hpp"\n#include "judge.hpp"\n#line 1 "tests.cpp"\n' + cfg.tests,
      };
      const c = await compile(files, ({ done, total }) => {
        const pct = Math.min(100, Math.round((100 * done) / total));
        status.textContent = pct < 100 ? `Downloading compiler… ${pct}%` : "Compiling…";
      });
      const warnings = (c.output || "").trim();
      if (!c.ok) {
        let text = warnings;
        if (c.internal) text = `The in-browser compiler failed to load. Reload the page to try again.\n\n${text}`;
        else if (UNSUPPORTED.test(text)) text += "\n\nThe in-browser compiler (WebAssembly) has no exceptions or threads, " +
          "so this problem can't be run here. Compile it locally with g++ -std=c++20 -pthread.";
        return finish(false, c.internal ? "Compiler unavailable" : "Compile error", text);
      }
      status.textContent = "Running…";
      let out = "";
      const r = await execute(c.wasm, (t) => { out += t; setOutput(out); });
      const timing = `compile ${c.ms} ms · run ${r.ms ?? RUN_TIMEOUT_MS} ms · Clang ${clangVersion} (WebAssembly)`;
      let text = out.trim();
      if (r.timedOut) text += `\nTime limit exceeded (${RUN_TIMEOUT_MS / 1000}s).`;
      if (r.error) text += `\n${r.error}`;
      if (warnings) text = `${warnings}\n\n${text}`;
      const ok = r.code === 0 && !r.error;
      finish(ok, ok ? "All tests passed" : r.timedOut ? "Time limit exceeded" : "Tests failed", `${text.trim()}\n\n${timing}`);
    }

    let running = false;
    async function run() {
      if (running || !hasWasm) return;
      running = true;
      runBtn.disabled = true;
      setOutput("");
      try {
        await runInBrowser();
      } catch (err) {
        finish(false, "Error", String(err && err.message || err));
      } finally {
        running = false;
        runBtn.disabled = !hasWasm;
      }
    }
    runBtn.addEventListener("click", run);
  }
})();
