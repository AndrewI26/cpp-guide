"""HTML page templates (plain f-strings, no template engine needed)."""
from __future__ import annotations

import html
import json

from .content import Lesson, Problem, Project
from .md import render as md_render

SITE_NAME = "cpp-guide"
TAGLINE = "High-performance C++ for quantitative finance"
e = html.escape


def layout(title: str, body: str, base: str, active: str = "", sidebar: str = "", toc: str = "",
           scripts: tuple[str, ...] = (), body_class: str = "") -> str:
    def nav(href: str, label: str, key: str) -> str:
        cls = ' class="active"' if key == active else ""
        return f'<a href="{base}{href}"{cls}>{label}</a>'

    script_tags = "".join(f'<script src="{base}/static/{s}"></script>' for s in ("app.js", *scripts))
    cols = "layout" + (" has-sidebar" if sidebar else "") + (" has-toc" if toc else "")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)} · {SITE_NAME}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Source+Serif+4:opsz,wght@8..60,400;8..60,500&display=swap">
<link rel="stylesheet" href="{base}/static/style.css">
<script>try{{var t=localStorage.getItem('theme');if(t)document.documentElement.dataset.theme=t;}}catch(_){{}}</script>
</head>
<body class="{body_class}" data-base="{base}">
<header class="topbar">
  <a class="wordmark" href="{base}/">{SITE_NAME}</a>
  <span class="topbar-tag">C++ for quants</span>
  <nav class="topnav">
    {nav('/lessons/', 'Textbook', 'lessons')}
    {nav('/projects/', 'Projects', 'projects')}
    {nav('/practice/', 'Practice', 'practice')}
  </nav>
  <button class="icon-btn theme-toggle" id="theme-toggle" type="button" aria-label="Switch theme">
    <svg class="icon-moon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/></svg>
    <svg class="icon-sun" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>
  </button>
</header>
<div class="{cols}">
{sidebar}
<main class="content">
{body}
</main>
{toc}
</div>
<footer class="footer">{SITE_NAME} · {TAGLINE}</footer>
{script_tags}
</body>
</html>
"""


# ---------------------------------------------------------------- lessons
def lessons_sidebar(parts: list[dict], current: Lesson | None, base: str) -> str:
    out = ['<aside class="sidebar"><nav>']
    for part in parts:
        out.append(f'<div class="side-part">{e(part["title"])}</div>')
        for module in part.get("modules", []):
            is_open = current is not None and current.module is module
            out.append(f'<details class="side-module"{" open" if is_open else ""}>'
                       f'<summary><span class="num">{module["number"]}</span>{e(module["title"])}</summary><ul>')
            for les in module["lessons_objs"]:
                if les.available:
                    cls = "current" if les is current else ""
                    out.append(f'<li class="{cls}"><a href="{base}{les.url}" data-lesson="{e(les.url)}">'
                               f'<span class="num">{les.number}</span>{e(les.title)}</a></li>')
                else:
                    out.append(f'<li class="planned"><span><span class="num">{les.number}</span>{e(les.title)}</span></li>')
            out.append("</ul></details>")
    out.append("</nav></aside>")
    return "".join(out)


def toc_html(toc: list[tuple[int, str, str]]) -> str:
    if len(toc) < 2:
        return ""
    items = "".join(f'<li class="toc-l{lvl}"><a href="#{hid}">{text}</a></li>' for lvl, hid, text in toc)
    return f'<aside class="toc"><div class="toc-title">On this page</div><ul>{items}</ul></aside>'


def curriculum_page(parts: list[dict], lessons: list[Lesson], base: str) -> str:
    written = sum(1 for l in lessons if l.available)
    body = [f"""
<section class="page-hero">
  <p class="eyebrow">The textbook</p>
  <h1>From first principles to the hot path.</h1>
  <p class="lede">{len(lessons)} chapters across {sum(len(p.get('modules', [])) for p in parts)} modules.
  Each chapter opens with why the idea matters, builds it up step by step, and ends every section with
  exercises. {written} chapters are written so far; the rest are planned and shown greyed out.</p>
  <div class="progress-line"><div class="progress-bar"><span id="lesson-progress-bar"></span></div>
  <span id="lesson-progress-text" data-total="{written}"></span></div>
</section>"""]
    for part in parts:
        body.append(f'<section class="part"><h2 class="part-title">{e(part["title"])}</h2>'
                    f'<p class="part-summary">{e(part.get("summary", ""))}</p><div class="module-grid">')
        for module in part.get("modules", []):
            body.append(f'<article class="module-card"><div class="module-num">Module {module["number"]}</div>'
                        f'<h3>{e(module["title"])}</h3><p>{e(module.get("summary", ""))}</p><ol class="lesson-list">')
            for les in module["lessons_objs"]:
                summary = f'<span class="lesson-summary">{e(les.summary)}</span>' if les.summary else ""
                if les.available:
                    body.append(f'<li><a href="{base}{les.url}" data-lesson="{e(les.url)}"><span class="num">{les.number}</span>'
                                f'<span><span class="lesson-title">{e(les.title)}</span>{summary}</span>'
                                f'<span class="check" aria-hidden="true"></span></a></li>')
                else:
                    body.append(f'<li class="planned"><span class="num">{les.number}</span><span>'
                                f'<span class="lesson-title">{e(les.title)}</span>{summary}</span>'
                                f'<span class="soon">Planned</span></li>')
            body.append("</ol></article>")
        body.append("</div></section>")
    return layout("Textbook", "".join(body), base, "lessons", body_class="page-curriculum")


def lesson_page(parts: list[dict], lesson: Lesson, prev: Lesson | None, nxt: Lesson | None, base: str) -> str:
    content, toc = md_render(lesson.body)
    minutes = lesson.meta.get("minutes")
    tags = lesson.meta.get("practice_tags", [])
    practice = ""
    if tags:
        links = " ".join(f'<a class="chip" href="{base}/practice/?tag={e(t)}">{e(t)}</a>' for t in tags)
        practice = (f'<div class="practice-cta"><div><h3>Put it into practice</h3>'
                    f'<p>Graded problems that use the ideas from this chapter.</p></div><div class="chips">{links}</div></div>')

    def pager(les: Lesson | None, label: str, cls: str) -> str:
        if not les:
            return "<span></span>"
        return (f'<a class="pager-link {cls}" href="{base}{les.url}"><span class="pager-label">{label}</span>'
                f'<span class="pager-title">{les.number} · {e(les.title)}</span></a>')

    meta_bits = [f"Module {lesson.module_index} · {e(lesson.module['title'])}"]
    if minutes:
        meta_bits.append(f"{minutes} min read")
    body = f"""
<article class="lesson" data-lesson="{e(lesson.url)}">
  <header class="lesson-header">
    <p class="eyebrow">{e(lesson.part_title)}</p>
    <h1><span class="chapter-num">{lesson.number}</span>{e(lesson.title)}</h1>
    <p class="lesson-meta">{' · '.join(meta_bits)}</p>
  </header>
  <div class="prose">{content}</div>
  {practice}
  <div class="complete-row"><button class="btn btn-primary" id="mark-complete" type="button">Mark chapter complete</button></div>
  <nav class="pager">{pager(prev, '← Previous', 'prev')}{pager(nxt, 'Next →', 'next')}</nav>
</article>"""
    return layout(lesson.title, body, base, "lessons", lessons_sidebar(parts, lesson, base), toc_html(toc))


# ---------------------------------------------------------------- projects
def _module_titles(parts: list[dict]) -> dict[str, tuple[int, str]]:
    return {m["slug"]: (m["number"], m["title"]) for p in parts for m in p.get("modules", [])}


def projects_index(projects: list[Project], parts: list[dict], base: str) -> str:
    mods = _module_titles(parts)
    cards = []
    for pr in projects:
        uses = "".join(f'<span class="mini-tag">{mods[u][0]} · {e(mods[u][1])}</span>' if u in mods else ""
                       for u in pr.uses)
        inner = (f'<div class="project-top"><span class="module-num">Project {pr.num}</span>{badge(pr.difficulty)}</div>'
                 f'<h3>{e(pr.title)}</h3><p>{e(pr.summary)}</p><div class="tags-cell">{uses}</div>')
        if pr.available:
            cards.append(f'<a class="project-card" href="{base}{pr.url}" data-lesson="{e(pr.url)}">{inner}'
                         f'<span class="project-go">Start project →</span></a>')
        else:
            cards.append(f'<div class="project-card planned">{inner}<span class="soon">Planned</span></div>')
    body = f"""
<section class="page-hero">
  <p class="eyebrow">Projects</p>
  <h1>Build the real thing.</h1>
  <p class="lede">The textbook teaches the ideas; projects are where you use them. Each project explains why the
  component matters, hands you the API to implement, and breaks the work into steps with hints you can reveal
  when you're stuck. There are no full solutions: you write the code, then test and benchmark it.</p>
</section>
<div class="project-grid">{''.join(cards)}</div>"""
    return layout("Projects", body, base, "projects", body_class="page-projects")


def project_page(pr: Project, parts: list[dict], prev: Project | None, nxt: Project | None, base: str) -> str:
    content, toc = md_render(pr.body)
    mods = _module_titles(parts)
    uses = " ".join(f'<a class="chip" href="{base}/lessons/">{mods[u][0]} · {e(mods[u][1])}</a>'
                    for u in pr.uses if u in mods)

    def pager(p: Project | None, label: str, cls: str) -> str:
        if not p:
            return "<span></span>"
        return (f'<a class="pager-link {cls}" href="{base}{p.url}"><span class="pager-label">{label}</span>'
                f'<span class="pager-title">{e(p.title)}</span></a>')

    minutes = pr.meta.get("hours")
    meta = f" · about {minutes} hours" if minutes else ""
    body = f"""
<article class="lesson" data-lesson="{e(pr.url)}">
  <header class="lesson-header">
    <p class="eyebrow"><a href="{base}/projects/" class="back-link">Projects</a></p>
    <h1><span class="chapter-num">Project {pr.num}</span>{e(pr.title)}</h1>
    <p class="lesson-meta">{badge(pr.difficulty)}{meta}</p>
    {f'<div class="uses"><span>Builds on</span>{uses}</div>' if uses else ''}
  </header>
  <div class="prose">{content}</div>
  <div class="complete-row"><button class="btn btn-primary" id="mark-complete" type="button">Mark project complete</button></div>
  <nav class="pager">{pager(prev, '← Previous project', 'prev')}{pager(nxt, 'Next project →', 'next')}</nav>
</article>"""
    return layout(pr.title, body, base, "projects", toc=toc_html(toc), body_class="page-project")


# ---------------------------------------------------------------- practice
def badge(difficulty: str) -> str:
    return f'<span class="badge badge-{difficulty}">{difficulty.title()}</span>'


def practice_index(problems: list[Problem], base: str) -> str:
    data = [{"num": p.num, "slug": p.slug, "title": p.title, "difficulty": p.difficulty,
             "tags": p.tags, "url": base + p.url} for p in problems]
    tags = sorted({t for p in problems for t in p.tags})
    counts = {d: sum(1 for p in problems if p.difficulty == d) for d in ("easy", "medium", "hard")}
    tag_chips = "".join(f'<button class="chip" type="button" data-tag="{e(t)}">{e(t)}</button>' for t in tags)
    body = f"""
<section class="page-hero">
  <p class="eyebrow">Practice</p>
  <h1>Sharpen the fundamentals.</h1>
  <p class="lede">{len(problems)} problems, each compiled and run against tests with AddressSanitizer and UBSan
  on your machine. Filter by difficulty or topic and track what you've solved.</p>
</section>
<section class="stats">
  <div class="stat"><div class="stat-num" id="stat-solved">0</div><div class="stat-label">Solved of {len(problems)}</div></div>
  <div class="stat"><div class="stat-num easy">{counts['easy']}</div><div class="stat-label">Easy</div></div>
  <div class="stat"><div class="stat-num medium">{counts['medium']}</div><div class="stat-label">Medium</div></div>
  <div class="stat"><div class="stat-num hard">{counts['hard']}</div><div class="stat-label">Hard</div></div>
</section>
<section class="filters">
  <input id="search" class="search" type="search" placeholder="Search problems…" autocomplete="off">
  <div class="segmented" id="difficulty-filter">
    <button type="button" data-difficulty="" class="active">All</button>
    <button type="button" data-difficulty="easy">Easy</button>
    <button type="button" data-difficulty="medium">Medium</button>
    <button type="button" data-difficulty="hard">Hard</button>
  </div>
  <select id="status-filter" class="select">
    <option value="">Any status</option><option value="solved">Solved</option><option value="unsolved">Unsolved</option>
  </select>
  <button class="btn btn-primary" id="random-problem" type="button">Random unsolved</button>
</section>
<div class="chips tag-filter" id="tag-filter">{tag_chips}</div>
<div class="problem-table-wrap">
<table class="problem-table">
  <thead><tr><th class="col-status"></th><th class="col-num">#</th><th>Title</th><th class="col-tags">Topics</th><th class="col-diff">Difficulty</th></tr></thead>
  <tbody id="problem-rows"></tbody>
</table>
<p class="empty" id="empty-state" hidden>No problems match those filters.</p>
</div>
<script id="problem-data" type="application/json">{json.dumps(data).replace("</", "<\\/")}</script>"""
    return layout("Practice", body, base, "practice", scripts=("practice.js",), body_class="page-practice")


SPECIAL_SECTIONS = {"Starter Code", "Hints", "Solution", "Tests"}


def problem_page(p: Problem, prev: Problem | None, nxt: Problem | None, base: str) -> str:
    statement = "".join(
        (f"<h2>{e(name)}</h2>" if name != "Problem" else "") + md_render(text)[0]
        for name, text in p.sections.items() if name not in SPECIAL_SECTIONS
    )
    extras = []
    if "Hints" in p.sections:
        extras.append(f'<details class="reveal"><summary>Hints</summary><div class="prose">{md_render(p.sections["Hints"])[0]}</div></details>')
    if "Solution" in p.sections:
        extras.append(f'<details class="reveal reveal-solution"><summary>Solution &amp; discussion</summary>'
                      f'<div class="prose">{md_render(p.sections["Solution"])[0]}</div></details>')
    if "Tests" in p.sections:
        extras.append(f'<details class="reveal"><summary>Test harness</summary><div class="prose">{md_render(p.sections["Tests"])[0]}</div></details>')
    tags = "".join(f'<a class="chip" href="{base}/practice/?tag={e(t)}">{e(t)}</a>' for t in p.tags)
    nav_prev = f'<a href="{base}{prev.url}">← #{prev.num}</a>' if prev else "<span></span>"
    nav_next = f'<a href="{base}{nxt.url}">#{nxt.num} →</a>' if nxt else "<span></span>"
    body = f"""
<div class="problem" data-slug="{e(p.slug)}">
  <section class="problem-statement">
    <a class="back-link" href="{base}/practice/">← All problems</a>
    <h1><span class="problem-num">#{p.num}</span>{e(p.title)}</h1>
    <div class="problem-meta">{badge(p.difficulty)}<span class="solved-flag" id="solved-flag" hidden>✓ Solved</span><div class="chips">{tags}</div></div>
    <div class="prose">{statement}</div>
    {''.join(extras)}
    <nav class="problem-nav">{nav_prev}{nav_next}</nav>
  </section>
  <section class="workspace">
    <div class="editor-head"><span>solution.hpp</span>
      <div class="editor-actions">
        <button class="btn btn-ghost" id="reset-code" type="button">Reset</button>
        <button class="btn btn-ghost" id="toggle-solved" type="button">Mark solved</button>
        <button class="btn btn-primary" id="run-code" type="button">Run tests <kbd>Ctrl ↵</kbd></button>
      </div>
    </div>
    <textarea id="editor" class="editor" spellcheck="false" autocapitalize="off" autocomplete="off">{e(p.starter)}</textarea>
    <div class="output-head"><span>Output</span><span id="run-status" class="run-status"></span></div>
    <pre id="output" class="output">Press “Run tests” to compile with g++ -std=c++20 -O2 and AddressSanitizer + UBSan.</pre>
  </section>
</div>
<script id="starter-code" type="text/plain">{e(p.starter)}</script>"""
    return layout(f"#{p.num} {p.title}", body, base, "practice", scripts=("practice.js",), body_class="page-problem")


# -------------------------------------------------------------------- home
def home_page(lessons: list[Lesson], problems: list[Problem], projects: list[Project], intro_md: str, base: str) -> str:
    first = next((l for l in lessons if l.available), None)
    start_href = f"{base}{first.url}" if first else f"{base}/lessons/"
    intro, _ = md_render(intro_md)
    counts = {d: sum(1 for p in problems if p.difficulty == d) for d in ("easy", "medium", "hard")}
    body = f"""
<section class="hero">
  <p class="eyebrow">{TAGLINE}</p>
  <h1>Write C++ that's correct first, then fast.</h1>
  <p class="lede">A textbook and problem set that goes from how a program is compiled to lock-free queues,
  cache-friendly order books and SIMD pricing kernels.</p>
  <div class="hero-actions">
    <a class="btn btn-primary btn-lg" href="{start_href}">Start reading</a>
    <a class="btn btn-secondary btn-lg" href="{base}/practice/">Practice problems</a>
  </div>
</section>
<section class="feature-grid">
  <a class="feature" href="{base}/lessons/"><div class="feature-num">{len(lessons)}</div><h3>Chapters</h3>
    <p>Each one starts with why the idea matters, then builds it up, with exercises at the end of every section.</p></a>
  <a class="feature" href="{base}/practice/"><div class="feature-num">{len(problems)}</div><h3>Practice problems</h3>
    <p>{counts['easy']} easy · {counts['medium']} medium · {counts['hard']} hard. Compiled and tested locally.</p></a>
  <a class="feature" href="{base}/projects/"><div class="feature-num">{len(projects)}</div><h3>Projects</h3>
    <p>Order books, parsers, lock-free pipelines, pricers. You get the API and hints, and you write the code.</p></a>
</section>
<section class="prose home-intro">{intro}</section>"""
    return layout("Home", body, base, "home", body_class="page-home")
