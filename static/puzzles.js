// Puzzles: bite-sized multiple-choice questions with an Elo rating kept in localStorage.
//
// Rating model (standard Elo, as used for chess puzzles): solving a puzzle is a "win" against the puzzle's rating.
//   expected = 1 / (1 + 10^((puzzle - you) / 400))
//   change   = K * (score - expected)            score = 1 if solved, 0 if not
// K starts high so a new player's rating settles quickly, then shrinks. Only the first try at a puzzle is rated.
(function () {
  const store = window.cppGuideStore;
  const dataEl = document.getElementById("puzzle-data");
  if (!dataEl) return;

  const PUZZLES = JSON.parse(dataEl.textContent);
  const BY_ID = new Map(PUZZLES.map((p) => [p.id, p]));
  const TOPIC_LABEL = { cpp: "C++", os: "OS", concurrency: "Concurrency" };
  const START = 1200, FLOOR = 100, HISTORY = 60, RECENT = 6;

  const kFactor = (games) => (games < 10 ? 40 : games < 40 ? 24 : 16);
  const expected = (you, puzzle) => 1 / (1 + Math.pow(10, (puzzle - you) / 400));
  function ratingChange(you, puzzle, solved, games) {
    const raw = kFactor(games) * ((solved ? 1 : 0) - expected(you, puzzle));
    const d = Math.round(raw);
    return d === 0 ? (solved ? 1 : -1) : d;   // every rated result moves you at least a point
  }

  // ---------------------------------------------------------------- state
  const fresh = () => ({ rating: START, best: START, games: 0, solved: 0, streak: 0,
                         attempts: {}, history: [START], recent: [] });
  let state = Object.assign(fresh(), store.get("puzzles", {}));
  const save = () => store.set("puzzles", state);
  const allTopics = [...new Set(PUZZLES.map((p) => p.topic))];
  let topics = new Set(store.get("puzzleTopics", allTopics).filter((t) => allTopics.includes(t)));

  const $ = (id) => document.getElementById(id);
  const el = {
    card: $("puzzle-card"), empty: $("puzzle-empty"), topic: $("puzzle-topic"), prating: $("puzzle-rating"),
    rated: $("puzzle-rated"), prompt: $("puzzle-prompt"), code: $("puzzle-code"), choices: $("puzzle-choices"),
    result: $("puzzle-result"), verdict: $("puzzle-verdict"), explain: $("puzzle-explain"), hint: $("puzzle-hint"),
    next: $("next-puzzle"), rating: $("my-rating"), delta: $("my-delta"), spark: $("rating-spark"),
    solved: $("stat-solved"), streak: $("stat-streak"), best: $("stat-best"), recent: $("recent-list"),
  };

  let current = null;      // puzzle being shown
  let answered = false;

  // ------------------------------------------------------------- choosing
  const randomOf = (arr) => arr[Math.floor(Math.random() * arr.length)];

  function pickPuzzle() {
    const pool = PUZZLES.filter((p) => topics.has(p.topic) && p !== current);
    if (!pool.length) return current && topics.has(current.topic) ? current : null;
    // Prefer unseen puzzles near your rating, widening the window until there are a few to choose from.
    const unseen = pool.filter((p) => !(p.id in state.attempts));
    if (unseen.length) {
      for (let window = 150; ; window += 100) {
        const near = unseen.filter((p) => Math.abs(p.rating - state.rating) <= window);
        if (near.length >= 3 || near.length === unseen.length || window > 2500) return randomOf(near.length ? near : unseen);
      }
    }
    // Everything has been seen: replay (unrated), avoiding the most recent few.
    const recentIds = new Set(state.recent.map((r) => r.id));
    const older = pool.filter((p) => !recentIds.has(p.id));
    return randomOf(older.length ? older : pool);
  }

  // ------------------------------------------------------------ rendering
  function shuffled(n) {
    const a = [...Array(n).keys()];
    for (let i = n - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [a[i], a[j]] = [a[j], a[i]];
    }
    return a;
  }

  function show(puzzle) {
    current = puzzle;
    answered = false;
    el.card.hidden = !puzzle;
    el.empty.hidden = !!puzzle;
    if (!puzzle) return;
    history.replaceState(null, "", `#${puzzle.id}`);

    const rated = !(puzzle.id in state.attempts);
    el.topic.textContent = TOPIC_LABEL[puzzle.topic] || puzzle.topic;
    el.topic.dataset.topic = puzzle.topic;
    el.prating.hidden = true;
    el.rated.textContent = rated ? "Rated" : "Unrated · seen before";
    el.rated.classList.toggle("unrated", !rated);
    el.prompt.innerHTML = puzzle.prompt;
    el.code.hidden = !puzzle.code;
    el.code.querySelector("code").innerHTML = puzzle.code;

    el.choices.innerHTML = "";
    shuffled(puzzle.choices.length).forEach((orig, pos) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "choice";
      b.dataset.index = orig;
      b.innerHTML = `<span class="choice-key">${pos + 1}</span><span class="choice-text">${puzzle.choices[orig]}</span>`;
      b.addEventListener("click", () => answer(orig));
      el.choices.appendChild(b);
    });

    el.result.hidden = true;
    el.next.hidden = true;
    el.hint.hidden = false;
    el.card.classList.remove("is-correct", "is-wrong");
    el.card.classList.add("enter");
    requestAnimationFrame(() => requestAnimationFrame(() => el.card.classList.remove("enter")));
  }

  function answer(index) {
    if (answered || !current) return;
    answered = true;
    const p = current;
    const ok = index === p.answer;
    const rated = !(p.id in state.attempts);
    let delta = 0;

    if (rated) {
      delta = ratingChange(state.rating, p.rating, ok, state.games);
      state.rating = Math.max(FLOOR, state.rating + delta);
      state.best = Math.max(state.best, state.rating);
      state.games += 1;
      if (ok) state.solved += 1;
      state.attempts[p.id] = ok ? 1 : 0;
      state.history = [...state.history, state.rating].slice(-HISTORY);
    }
    state.streak = ok ? state.streak + 1 : 0;
    state.recent = [{ id: p.id, ok, delta, rated }, ...state.recent].slice(0, RECENT);
    save();

    for (const b of el.choices.querySelectorAll(".choice")) {
      const i = Number(b.dataset.index);
      b.disabled = true;
      if (i === p.answer) b.classList.add("correct");
      else if (i === index) b.classList.add("wrong");
      else b.classList.add("dim");
    }
    el.card.classList.add(ok ? "is-correct" : "is-wrong");
    const deltaText = rated ? ` <span class="verdict-delta">${fmtDelta(delta)}</span>` : "";
    el.verdict.innerHTML = (ok ? "Correct!" : "Not quite.") + deltaText;
    el.verdict.className = `puzzle-verdict ${ok ? "good" : "bad"}`;
    el.explain.innerHTML = p.explain;
    el.prating.textContent = `Puzzle rating ${p.rating}`;
    el.prating.hidden = false;
    el.result.hidden = false;
    el.hint.hidden = true;
    el.next.hidden = false;
    el.next.focus({ preventScroll: true });
    paintStats(rated ? delta : null);
  }

  const fmtDelta = (d) => (d > 0 ? `+${d}` : `−${Math.abs(d)}`);

  function paintStats(delta) {
    el.rating.textContent = state.rating;
    el.best.textContent = state.best;
    el.streak.textContent = state.streak;
    el.solved.textContent = `${state.solved} / ${state.games}`;
    if (delta !== null && delta !== undefined) {
      el.delta.textContent = fmtDelta(delta);
      el.delta.className = `rating-delta ${delta > 0 ? "good" : "bad"} pop`;
    } else {
      el.delta.textContent = "";
      el.delta.className = "rating-delta";
    }
    paintSpark();
    paintRecent();
  }

  function paintSpark() {
    const h = state.history;
    const svg = el.spark;
    if (h.length < 2) { svg.innerHTML = ""; return; }
    const W = 240, H = 56, pad = 4;
    const lo = Math.min(...h), hi = Math.max(...h);
    const span = Math.max(hi - lo, 40);
    const mid = (hi + lo) / 2;
    const x = (i) => (i / (h.length - 1)) * W;
    const y = (v) => H - pad - ((v - (mid - span / 2)) / span) * (H - 2 * pad);
    const pts = h.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
    const up = h[h.length - 1] >= h[0];
    svg.innerHTML =
      `<polyline class="spark-area ${up ? "up" : "down"}" points="0,${H} ${pts} ${W},${H}"/>` +
      `<polyline class="spark-line ${up ? "up" : "down"}" points="${pts}" vector-effect="non-scaling-stroke"/>`;
  }

  function paintRecent() {
    el.recent.innerHTML = state.recent.map((r) => {
      const p = BY_ID.get(r.id);
      if (!p) return "";
      const d = r.rated ? `<span class="recent-delta ${r.delta > 0 ? "good" : "bad"}">${fmtDelta(r.delta)}</span>`
                        : `<span class="recent-delta">unrated</span>`;
      return `<li><a href="#${p.id}" data-id="${p.id}"><span class="recent-mark ${r.ok ? "good" : "bad"}">${r.ok ? "✓" : "✗"}</span>` +
             `<span class="recent-topic">${TOPIC_LABEL[p.topic]}</span><span class="recent-rating">${p.rating}</span>${d}</a></li>`;
    }).join("");
  }

  // --------------------------------------------------------------- events
  el.next.addEventListener("click", () => show(pickPuzzle()));

  el.recent.addEventListener("click", (ev) => {
    const a = ev.target.closest("a[data-id]");
    if (!a) return;
    ev.preventDefault();
    const p = BY_ID.get(a.dataset.id);
    if (p) show(p);
  });

  document.querySelectorAll("#topic-filter .chip").forEach((chip) => {
    const t = chip.dataset.topic;
    const paint = () => { chip.classList.toggle("active", topics.has(t)); chip.setAttribute("aria-pressed", topics.has(t)); };
    paint();
    chip.addEventListener("click", () => {
      topics.has(t) ? topics.delete(t) : topics.add(t);
      store.set("puzzleTopics", [...topics]);
      paint();
      // Leave an answered puzzle (and its explanation) on screen; "Next" respects the new filter.
      // Switch away from an unanswered one only if its topic was just turned off.
      if (!current) show(pickPuzzle());
      else if (!answered && !topics.has(current.topic)) show(pickPuzzle());
    });
  });

  $("reset-rating").addEventListener("click", () => {
    if (!confirm("Reset your puzzle rating to 1200 and forget which puzzles you've seen?")) return;
    state = fresh();
    save();
    paintStats(null);
    show(pickPuzzle());
  });

  document.addEventListener("keydown", (ev) => {
    if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
    if (ev.target.closest && ev.target.closest("input, textarea, select")) return;
    if (!answered && /^[1-9]$/.test(ev.key)) {
      const b = el.choices.querySelectorAll(".choice")[Number(ev.key) - 1];
      if (b) { ev.preventDefault(); b.click(); }
    } else if (answered && (ev.key === "Enter" || ev.key === "n" || ev.key === "N")) {
      if (ev.key === "Enter" && ev.target === el.next) return;   // the button's own click handles it
      ev.preventDefault();
      show(pickPuzzle());
    }
  });

  window.addEventListener("hashchange", () => {
    const p = BY_ID.get(location.hash.slice(1));
    if (p && p !== current) show(p);
  });

  // ---------------------------------------------------------------- start
  paintStats(null);
  const linked = BY_ID.get(location.hash.slice(1));
  show(linked || pickPuzzle());
})();
