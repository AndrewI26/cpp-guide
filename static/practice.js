// Practice list filtering and the problem-page editor / test runner.
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
    const editor = document.getElementById("editor");
    const output = document.getElementById("output");
    const status = document.getElementById("run-status");
    const runBtn = document.getElementById("run-code");
    const solvedBtn = document.getElementById("toggle-solved");
    const flag = document.getElementById("solved-flag");
    const starter = document.getElementById("starter-code").textContent;
    const codeKey = `code:${slug}`;

    const saved = store.get(codeKey, null);
    if (saved !== null) editor.value = saved;

    function paintSolved() {
      const s = solved.has(slug);
      flag.hidden = !s;
      solvedBtn.textContent = s ? "✓ Solved" : "Mark solved";
      solvedBtn.classList.toggle("is-solved", s);
    }
    paintSolved();

    let saveTimer;
    editor.addEventListener("input", () => {
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
    });

    solvedBtn.addEventListener("click", () => {
      solved.has(slug) ? solved.delete(slug) : solved.add(slug);
      saveSolved();
      paintSolved();
    });

    async function run() {
      runBtn.disabled = true;
      status.className = "run-status";
      status.textContent = "Compiling…";
      output.textContent = "";
      try {
        const res = await fetch("/api/run", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ slug, code: editor.value }),
        });
        if (res.status === 401) throw new Error("Running code on this site requires the runner password.");
        if (!res.ok) {
          let msg = `HTTP ${res.status}`;
          try { msg = (await res.json()).error || msg; } catch (_) {}
          throw new Error(msg);
        }
        const r = await res.json();
        const timing = r.run_ms !== undefined ? `compile ${r.compile_ms} ms · run ${r.run_ms} ms` : "";
        if (r.ok) {
          status.className = "run-status pass";
          status.textContent = "All tests passed";
          solved.add(slug); saveSolved(); paintSolved();
        } else {
          status.className = "run-status fail";
          status.textContent = r.stage === "compile" ? "Compile error" : r.stage === "run" ? "Tests failed" : "Error";
        }
        output.textContent = (r.output || "").trim() + (timing ? `\n\n${timing}` : "");
      } catch (err) {
        status.className = "run-status fail";
        status.textContent = "Runner unavailable";
        output.textContent = err.message + "\n\nYou can always run problems locally:\n\n    uv run cpp-guide serve";
      } finally {
        runBtn.disabled = false;
      }
    }
    runBtn.addEventListener("click", run);
  }
})();
