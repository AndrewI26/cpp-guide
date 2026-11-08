"""Build the static site into site/."""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from . import render
from .content import CONTENT, ROOT, load_curriculum, load_problems, load_projects

SITE = ROOT / "site"
STATIC = ROOT / "static"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def build(base: str = "", quiet: bool = False) -> Path:
    t0 = time.perf_counter()
    base = base.rstrip("/")
    tmp = ROOT / "site.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()

    parts, lessons = load_curriculum()
    problems = load_problems()
    written = [l for l in lessons if l.available]

    intro = (CONTENT / "index.md").read_text() if (CONTENT / "index.md").exists() else ""
    projects = load_projects()
    _write(tmp / "index.html", render.home_page(lessons, problems, projects, intro, base))
    _write(tmp / "projects" / "index.html", render.projects_index(projects, parts, base))
    ready = [p for p in projects if p.available]
    for i, pr in enumerate(ready):
        prev = ready[i - 1] if i > 0 else None
        nxt = ready[i + 1] if i + 1 < len(ready) else None
        _write(tmp / pr.url.lstrip("/"), render.project_page(pr, parts, prev, nxt, base))
    _write(tmp / "lessons" / "index.html", render.curriculum_page(parts, lessons, base))
    for i, les in enumerate(written):
        prev = written[i - 1] if i > 0 else None
        nxt = written[i + 1] if i + 1 < len(written) else None
        _write(tmp / les.url.lstrip("/"), render.lesson_page(parts, les, prev, nxt, base))

    _write(tmp / "practice" / "index.html", render.practice_index(problems, base))
    for i, p in enumerate(problems):
        prev = problems[i - 1] if i > 0 else None
        nxt = problems[i + 1] if i + 1 < len(problems) else None
        _write(tmp / p.url.lstrip("/"), render.problem_page(p, prev, nxt, base))

    shutil.copytree(STATIC, tmp / "static")

    # Swap atomically-ish so a running server never sees a half-built site.
    old = ROOT / "site.old"
    shutil.rmtree(old, ignore_errors=True)
    if SITE.exists():
        SITE.rename(old)
    tmp.rename(SITE)
    shutil.rmtree(old, ignore_errors=True)

    if not quiet:
        ms = (time.perf_counter() - t0) * 1000
        print(f"Built {len(written)} lessons ({len(lessons)} planned) and {len(problems)} problems "
              f"into {SITE.relative_to(ROOT)}/ in {ms:.0f} ms")
    return SITE


def snapshot() -> dict[str, float]:
    """mtimes of everything that affects the build (used by the dev server's watcher)."""
    files: dict[str, float] = {}
    for folder in (CONTENT, STATIC, ROOT / "sitegen"):
        for p in folder.rglob("*"):
            if p.is_file():
                files[str(p)] = p.stat().st_mtime
    return files
