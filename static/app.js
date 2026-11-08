// Shared behaviour: theme toggle, copy buttons, lesson progress, TOC highlighting.
(function () {
  const store = {
    get(key, fallback) {
      try { const v = localStorage.getItem(key); return v === null ? fallback : JSON.parse(v); }
      catch (_) { return fallback; }
    },
    set(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch (_) {} },
  };
  window.cppGuideStore = store;

  // theme
  const toggle = document.getElementById("theme-toggle");
  if (toggle) {
    toggle.addEventListener("click", () => {
      const root = document.documentElement;
      const current = root.dataset.theme ||
        (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
      const next = current === "dark" ? "light" : "dark";
      root.dataset.theme = next;
      try { localStorage.setItem("theme", next); } catch (_) {}
    });
  }

  // copy buttons
  document.querySelectorAll(".copy-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const code = btn.closest(".code-block").querySelector("code").innerText;
      try { await navigator.clipboard.writeText(code); btn.textContent = "Copied"; }
      catch (_) { btn.textContent = "Press Ctrl+C"; }
      setTimeout(() => (btn.textContent = "Copy"), 1400);
    });
  });

  // lesson progress
  const done = new Set(store.get("lessonsDone", []));
  function paintLessons() {
    document.querySelectorAll("a[data-lesson]").forEach((a) =>
      a.classList.toggle("done", done.has(a.dataset.lesson)));
    const text = document.getElementById("lesson-progress-text");
    if (text) {
      const total = Number(text.dataset.total) || 0;
      const n = [...done].filter((u) => document.querySelector(`a[data-lesson="${u}"]`)).length;
      text.textContent = `${n} of ${total} written chapters complete`;
      document.getElementById("lesson-progress-bar").style.width = total ? `${(100 * n) / total}%` : "0";
    }
    const btn = document.getElementById("mark-complete");
    const lesson = document.querySelector("article.lesson");
    if (btn && lesson) {
      const isDone = done.has(lesson.dataset.lesson);
      btn.classList.toggle("is-done", isDone);
      btn.textContent = isDone ? "✓ Chapter complete" : "Mark chapter complete";
    }
  }
  const markBtn = document.getElementById("mark-complete");
  if (markBtn) {
    markBtn.addEventListener("click", () => {
      const url = document.querySelector("article.lesson").dataset.lesson;
      done.has(url) ? done.delete(url) : done.add(url);
      store.set("lessonsDone", [...done]);
      paintLessons();
    });
  }
  paintLessons();

  // highlight the current section in the TOC
  const tocLinks = [...document.querySelectorAll(".toc a")];
  if (tocLinks.length && "IntersectionObserver" in window) {
    const byId = new Map(tocLinks.map((a) => [a.getAttribute("href").slice(1), a]));
    const obs = new IntersectionObserver((entries) => {
      entries.forEach((en) => {
        if (en.isIntersecting) {
          tocLinks.forEach((a) => a.classList.remove("active"));
          const link = byId.get(en.target.id);
          if (link) link.classList.add("active");
        }
      });
    }, { rootMargin: "-80px 0px -70% 0px" });
    byId.forEach((_, id) => { const el = document.getElementById(id); if (el) obs.observe(el); });
  }
})();
