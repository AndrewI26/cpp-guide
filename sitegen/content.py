"""Load the curriculum, lessons and practice problems from content/."""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTENT = ROOT / "content"
LESSONS_DIR = CONTENT / "lessons"
PRACTICE_DIR = CONTENT / "practice"
PROJECTS_DIR = CONTENT / "projects"

FRONT_RE = re.compile(r"\A\+\+\+\s*\n(.*?)\n\+\+\+\s*\n?", re.S)
SECTION_RE = re.compile(r"^##[ \t]+(.+?)[ \t]*$", re.M)
CPP_BLOCK_RE = re.compile(r"^```(?:cpp|c\+\+|hpp)[^\n]*\n(.*?)\n```", re.S | re.M)
DIFFICULTIES = ("easy", "medium", "hard")


def split_front(text: str) -> tuple[dict, str]:
    m = FRONT_RE.match(text)
    if not m:
        return {}, text
    return tomllib.loads(m.group(1)), text[m.end():]


# ------------------------------------------------------------------ lessons
@dataclass
class Lesson:
    part_index: int
    part_title: str
    module_index: int
    module: dict
    slug: str
    title: str
    summary: str
    path: Path | None
    number: str = ""
    meta: dict = field(default_factory=dict)
    body: str = ""

    @property
    def available(self) -> bool:
        return self.path is not None

    @property
    def url(self) -> str:
        return f"/lessons/{self.module['slug']}/{self.slug}.html"


@dataclass
class Project:
    num: int
    slug: str
    title: str
    difficulty: str
    summary: str
    uses: list[str]
    path: Path | None
    meta: dict = field(default_factory=dict)
    body: str = ""

    @property
    def available(self) -> bool:
        return self.path is not None

    @property
    def url(self) -> str:
        return f"/projects/{self.slug}.html"


def load_projects() -> list[Project]:
    data = tomllib.loads((CONTENT / "curriculum.toml").read_text())
    projects = []
    for n, item in enumerate(data.get("projects", []), start=1):
        path = PROJECTS_DIR / f"{item['slug']}.md"
        proj = Project(n, item["slug"], item["title"], item.get("difficulty", "medium"),
                       item.get("summary", ""), list(item.get("uses", [])), path if path.exists() else None)
        if proj.path:
            proj.meta, proj.body = split_front(proj.path.read_text())
        projects.append(proj)
    return projects


def load_curriculum() -> tuple[list[dict], list[Lesson]]:
    data = tomllib.loads((CONTENT / "curriculum.toml").read_text())
    parts = data["parts"]
    lessons: list[Lesson] = []
    module_no = 0
    for p_idx, part in enumerate(parts):
        for module in part.get("modules", []):
            module_no += 1
            module["number"] = module_no
            module["lessons_objs"] = []
            for l_no, item in enumerate(module.get("lessons", []), start=1):
                path = LESSONS_DIR / module["slug"] / f"{item['slug']}.md"
                lesson = Lesson(
                    part_index=p_idx,
                    part_title=part["title"],
                    module_index=module_no,
                    module=module,
                    slug=item["slug"],
                    title=item["title"],
                    summary=item.get("summary", ""),
                    path=path if path.exists() else None,
                    number=f"{module_no}.{l_no}",
                )
                if lesson.path:
                    lesson.meta, lesson.body = split_front(lesson.path.read_text())
                    lesson.title = lesson.meta.get("title", lesson.title)
                module["lessons_objs"].append(lesson)
                lessons.append(lesson)
    return parts, lessons


# ----------------------------------------------------------------- practice
@dataclass
class Problem:
    num: int
    slug: str
    title: str
    difficulty: str
    tags: list[str]
    meta: dict
    sections: dict[str, str]
    starter: str
    solution: str
    tests: str

    @property
    def url(self) -> str:
        return f"/practice/{self.slug}.html"


def split_sections(body: str) -> dict[str, str]:
    pieces = SECTION_RE.split(body)
    sections: dict[str, str] = {}
    if pieces[0].strip():
        sections["Problem"] = pieces[0]
    for name, text in zip(pieces[1::2], pieces[2::2]):
        sections[name.strip()] = text
    return sections


def first_cpp(text: str) -> str:
    m = CPP_BLOCK_RE.search(text or "")
    return m.group(1) if m else ""


def load_problem(path: Path) -> Problem:
    meta, body = split_front(path.read_text())
    m = re.match(r"(\d+)-(.+)\.md$", path.name)
    if not m:
        raise ValueError(f"{path.name}: practice files must be named NNN-slug.md")
    difficulty = str(meta.get("difficulty", "")).lower()
    if difficulty not in DIFFICULTIES:
        raise ValueError(f"{path.name}: difficulty must be one of {DIFFICULTIES}")
    sections = split_sections(body)
    return Problem(
        num=int(m.group(1)),
        slug=m.group(2),
        title=meta["title"],
        difficulty=difficulty,
        tags=list(meta.get("tags", [])),
        meta=meta,
        sections=sections,
        starter=first_cpp(sections.get("Starter Code", "")),
        solution=first_cpp(sections.get("Solution", "")),
        tests=first_cpp(sections.get("Tests", "")),
    )


def load_problems() -> list[Problem]:
    return sorted((load_problem(p) for p in PRACTICE_DIR.glob("*.md")), key=lambda p: p.num)


def find_problem(slug: str) -> Problem | None:
    for path in PRACTICE_DIR.glob(f"*-{slug}.md"):
        prob = load_problem(path)
        if prob.slug == slug:
            return prob
    return None
