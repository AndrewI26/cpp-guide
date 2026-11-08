"""A small, dependency-free Markdown -> HTML converter.

Supports the subset this project uses: ATX headings (with anchor ids and a TOC),
paragraphs, **bold**, *italic*, `code`, ~~strike~~, links, images, fenced code
blocks (highlighted), nested ordered/unordered lists, blockquotes, GitHub-style
callouts (> [!NOTE], [!TIP], [!WARNING], [!PERF], [!QUANT]), tables, horizontal
rules and raw HTML blocks.
"""
from __future__ import annotations

import html
import re
import textwrap

from .highlight import highlight

FENCE_RE = re.compile(r"^(\s*)(`{3,}|~{3,})\s*([\w+#.-]*)\s*(.*)$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
HR_RE = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
LIST_RE = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$")
TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
CALLOUT_RE = re.compile(r"^\[!(NOTE|TIP|WARNING|IMPORTANT|PERF|QUANT)\]\s*(.*)$", re.I)
HTML_BLOCK_RE = re.compile(r"^<(/?)(div|details|summary|table|section|aside|figure|p|pre|ul|ol|iframe|svg|!--)", re.I)
CALLOUT_LABELS = {"perf": "Performance", "quant": "Quant Corner", "important": "Important"}


def slugify(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).lower()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"[\s_]+", "-", text).strip("-") or "section"


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


class Markdown:
    def __init__(self) -> None:
        self.toc: list[tuple[int, str, str]] = []
        self._ids: dict[str, int] = {}

    # ------------------------------------------------------------------ API
    def convert(self, text: str) -> str:
        self.toc, self._ids = [], {}
        self._exercise_count = 0
        self._step_count = 0
        lines = text.replace("\r\n", "\n").replace("\t", "    ").split("\n")
        return self._blocks(lines)

    # --------------------------------------------------------------- inline
    def inline(self, text: str) -> str:
        stash: list[str] = []

        def keep(fragment: str) -> str:
            stash.append(fragment)
            return f"\x00{len(stash) - 1}\x00"

        text = re.sub(r"(`+)(.+?)\1", lambda m: keep("<code>" + html.escape(m.group(2).strip(), quote=False) + "</code>"), text)
        text = re.sub(r"\\([\\`*_{}\[\]()#+\-.!|<>])", lambda m: keep(html.escape(m.group(1))), text)
        text = re.sub(r"<(https?://[^>\s]+)>", lambda m: keep(f'<a href="{html.escape(m.group(1))}">{html.escape(m.group(1))}</a>'), text)
        text = re.sub(r"</?(kbd|sup|sub|br|span|mark|abbr)\b[^>]*>", lambda m: keep(m.group(0)), text)
        text = html.escape(text, quote=False)
        text = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", r'<img src="\2" alt="\1" loading="lazy">', text)
        text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', text)
        text = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<strong>\1</strong>", text)
        text = re.sub(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])", r"<em>\1</em>", text)
        text = re.sub(r"~~(.+?)~~", r"<del>\1</del>", text)
        for _ in range(4):  # placeholders may nest (e.g. code inside a link label)
            if "\x00" not in text:
                break
            text = re.sub(r"\x00(\d+)\x00", lambda m: stash[int(m.group(1))], text)
        return text

    # --------------------------------------------------------------- blocks
    def _starts_block(self, lines: list[str], i: int) -> bool:
        line, s = lines[i], lines[i].strip()
        return bool(
            FENCE_RE.match(line) or HEADING_RE.match(s) or HR_RE.match(line)
            or s.startswith(">") or LIST_RE.match(line) or HTML_BLOCK_RE.match(s)
            or s.startswith(":::")
        )

    def _blocks(self, lines: list[str]) -> str:
        out: list[str] = []
        i, n = 0, len(lines)
        while i < n:
            line = lines[i]
            s = line.strip()
            if not s:
                i += 1
                continue
            m = FENCE_RE.match(line)
            if m:
                i = self._fence(lines, i, m, out)
                continue
            m = HEADING_RE.match(s)
            if m:
                out.append(self._heading(len(m.group(1)), m.group(2)))
                i += 1
                continue
            if HR_RE.match(line):
                out.append("<hr>")
                i += 1
                continue
            if s.startswith(">"):
                buf = []
                while i < n and lines[i].strip().startswith(">"):
                    buf.append(re.sub(r"^\s*>\s?", "", lines[i]))
                    i += 1
                out.append(self._blockquote(buf))
                continue
            if "|" in s and i + 1 < n and "-" in lines[i + 1] and TABLE_SEP_RE.match(lines[i + 1]):
                i = self._table(lines, i, out)
                continue
            if LIST_RE.match(line):
                i = self._list(lines, i, out)
                continue
            if s.startswith(":::exercise") or s.startswith(":::step"):
                i = self._exercise(lines, i, out)
                continue
            if HTML_BLOCK_RE.match(s):
                buf = []
                while i < n and lines[i].strip():
                    buf.append(lines[i])
                    i += 1
                out.append("\n".join(buf))
                continue
            buf = [s]
            i += 1
            while i < n and lines[i].strip() and not self._starts_block(lines, i):
                buf.append(lines[i].strip())
                i += 1
            out.append("<p>" + self.inline(" ".join(buf)) + "</p>")
        return "\n".join(out)

    def _exercise(self, lines: list[str], i: int, out: list[str]) -> int:
        """:::exercise Title / body / [:::hint / body]* / [:::solution / body] / :::

        `:::step` is the same block numbered "Step N" (used by projects). Several
        :::hint sections become separate, progressively revealed hints.
        """
        head = lines[i].strip()
        kind = "step" if head.startswith(":::step") else "exercise"
        title = head[len(":::" + kind):].strip() or kind.title()
        parts: dict[str, list] = {"question": [], "hint": [], "solution": []}
        hints: list[list[str]] = []
        cur = "question"
        i += 1
        in_code = False
        while i < len(lines):
            st = lines[i].strip()
            if FENCE_RE.match(lines[i]):
                in_code = not in_code
            if not in_code and st == ":::hint":
                cur = "hint"
                hints.append([])
            elif not in_code and st == ":::solution":
                cur = "solution"
            elif not in_code and st == ":::":
                i += 1
                break
            elif cur == "hint":
                hints[-1].append(lines[i])
            else:
                parts[cur].append(lines[i])
            i += 1
        if kind == "step":
            self._step_count = getattr(self, "_step_count", 0) + 1
            label, cls = f"Step {self._step_count}", "exercise step"
        else:
            self._exercise_count = getattr(self, "_exercise_count", 0) + 1
            label, cls = f"Q{self._exercise_count}", "exercise"
        html_parts = [
            f'<div class="{cls}"><div class="exercise-title"><span class="exercise-num">'
            f"{label}</span>{self.inline(title)}</div>",
            self._blocks(parts["question"]),
        ]
        for n, hint in enumerate(hints, start=1):
            name = "Hint" if len(hints) == 1 else f"Hint {n}"
            html_parts.append(f'<details class="hint"><summary>{name}</summary>{self._blocks(hint)}</details>')
        if parts["solution"]:
            html_parts.append(
                f'<details class="solution"><summary>Show solution</summary>{self._blocks(parts["solution"])}</details>'
            )
        html_parts.append("</div>")
        out.append("".join(html_parts))
        return i

    def _fence(self, lines: list[str], i: int, m: re.Match, out: list[str]) -> int:
        indent, fence, lang, info = len(m.group(1)), m.group(2), m.group(3).lower(), m.group(4)
        buf: list[str] = []
        i += 1
        while i < len(lines):
            l = lines[i]
            st = l.strip()
            if st.startswith(fence) and st.strip(fence[0]) == "":
                i += 1
                break
            buf.append(l[indent:] if l[:indent].strip() == "" else l.lstrip())
            i += 1
        caption = info.strip().strip('"')
        if lang == "layout":
            out.append(self.layout_diagram("\n".join(buf), caption))
        elif lang in ("svg", "diagram"):
            cap = f"<figcaption>{self.inline(caption)}</figcaption>" if caption else ""
            out.append(f'<figure class="fig">{chr(10).join(buf)}{cap}</figure>')
        else:
            out.append(self.code_block("\n".join(buf), lang, caption))
        return i

    def layout_diagram(self, src: str, caption: str = "") -> str:
        """Render a byte-level memory layout.

        Blocks separated by blank lines; each block may have `title: ...` and
        `row: N` (bytes per row, default 8) lines, then fields as `name:bytes`.
        Fields named `pad` are drawn hatched; `_` in names becomes a space.
        A field may carry a colour override: `qty:4:2` uses palette colour 2.
        """
        out = ['<figure class="fig layout-fig">']
        for block in re.split(r"\n\s*\n", src.strip()):
            title, row, fields = "", 8, []
            for line in block.splitlines():
                line = line.strip()
                if line.startswith("title:"):
                    title = line[6:].strip()
                elif line.startswith("row:"):
                    row = int(line[4:])
                elif line:
                    for tok in line.split():
                        m = re.match(r"^(.+?):(\d+)(?::(\d))?$", tok)
                        if not m:
                            raise ValueError(f"layout diagram: can't parse field {tok!r} (want name:bytes[:colour])")
                        fields.append((m.group(1), int(m.group(2)), int(m.group(3)) if m.group(3) else None))
            colors: dict[str, int] = {}
            rows: list[list[str]] = [[]]
            used = 0
            for name, size, color in fields:
                if name != "pad" and name not in colors:
                    colors[name] = color if color is not None else len(colors) % 6 + 1
                remaining = size
                while remaining > 0:
                    if used == row:
                        rows.append([])
                        used = 0
                    take = min(remaining, row - used)
                    label = html.escape(name.replace("_", " "))
                    cls = "seg pad" if name == "pad" else f"seg c{colors[name]}"
                    show = label if name != "pad" else ""
                    size_txt = f"<small>{size}B</small>" if remaining == size and name != "pad" else ""
                    rows[-1].append(f'<div class="{cls}" style="grid-column:span {take}" title="{label} ({size} bytes)">'
                                    f"<span>{show}</span>{size_txt}</div>")
                    used += take
                    remaining -= take
            total = sum(f[1] for f in fields)
            out.append('<div class="layout-struct">')
            if title:
                out.append(f'<div class="layout-title">{self.inline(title)}</div>')
            for r, cells in enumerate(rows):
                out.append(f'<div class="layout-row"><span class="layout-off">{r * row}</span>'
                           f'<div class="layout-cells" style="grid-template-columns:repeat({row},1fr)">{"".join(cells)}</div></div>')
            out.append(f'<div class="layout-foot">{total} bytes · {row} bytes per row</div></div>')
        if caption:
            out.append(f"<figcaption>{self.inline(caption)}</figcaption>")
        out.append("</figure>")
        return "".join(out)

    def code_block(self, code: str, lang: str, title: str = "") -> str:
        label = html.escape(title or lang or "text")
        return (
            f'<div class="code-block"><div class="code-head"><span>{label}</span>'
            f'<button class="copy-btn" type="button">Copy</button></div>'
            f'<pre class="lang-{html.escape(lang or "text")}"><code>{highlight(code.rstrip(), lang)}</code></pre></div>'
        )

    def _heading(self, level: int, text: str) -> str:
        inner = self.inline(text)
        hid = slugify(inner)
        if hid in self._ids:
            self._ids[hid] += 1
            hid = f"{hid}-{self._ids[hid]}"
        else:
            self._ids[hid] = 0
        if level in (2, 3):
            self.toc.append((level, hid, re.sub(r"<[^>]+>", "", inner)))
        return f'<h{level} id="{hid}"><a class="anchor" href="#{hid}" aria-hidden="true">#</a>{inner}</h{level}>'

    def _blockquote(self, buf: list[str]) -> str:
        m = CALLOUT_RE.match(buf[0].strip()) if buf else None
        if m:
            kind = m.group(1).lower()
            title = m.group(2) or CALLOUT_LABELS.get(kind, kind.title())
            return (
                f'<div class="callout callout-{kind}"><div class="callout-title">{self.inline(title)}</div>'
                f"{self._blocks(buf[1:])}</div>"
            )
        return "<blockquote>" + self._blocks(buf) + "</blockquote>"

    @staticmethod
    def _split_row(line: str) -> list[str]:
        s = line.strip()
        if s.startswith("|"):
            s = s[1:]
        if s.endswith("|") and not s.endswith("\\|"):
            s = s[:-1]
        cells, cur, in_code, j = [], "", False, 0
        while j < len(s):
            c = s[j]
            if c == "\\" and j + 1 < len(s) and s[j + 1] == "|":
                cur += "|"
                j += 2
                continue
            if c == "`":
                in_code = not in_code
            if c == "|" and not in_code:
                cells.append(cur.strip())
                cur = ""
            else:
                cur += c
            j += 1
        cells.append(cur.strip())
        return cells

    def _table(self, lines: list[str], i: int, out: list[str]) -> int:
        header = self._split_row(lines[i])
        aligns = []
        for c in self._split_row(lines[i + 1]):
            left, right = c.startswith(":"), c.endswith(":")
            aligns.append("center" if left and right else "right" if right else "left" if left else "")
        i += 2
        rows = []
        while i < len(lines) and lines[i].strip() and "|" in lines[i]:
            rows.append(self._split_row(lines[i]))
            i += 1

        def cell(tag: str, text: str, k: int) -> str:
            a = aligns[k] if k < len(aligns) else ""
            style = f' style="text-align:{a}"' if a else ""
            return f"<{tag}{style}>{self.inline(text)}</{tag}>"

        head = "".join(cell("th", t, k) for k, t in enumerate(header))
        body = "".join("<tr>" + "".join(cell("td", t, k) for k, t in enumerate(r)) + "</tr>" for r in rows)
        out.append(f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>')
        return i

    def _list(self, lines: list[str], i: int, out: list[str]) -> int:
        m = LIST_RE.match(lines[i])
        base, ordered = len(m.group(1)), m.group(2)[0].isdigit()
        start = int(m.group(2)[:-1]) if ordered else 1
        items: list[list[str]] = []
        tight, n = True, len(lines)

        def sibling(line: str) -> bool:
            mm = LIST_RE.match(line)
            return bool(mm and len(mm.group(1)) == base and mm.group(2)[0].isdigit() == ordered)

        while i < n:
            line = lines[i]
            if sibling(line):
                items.append([LIST_RE.match(line).group(3)])
                i += 1
                continue
            if not line.strip():
                j = i + 1
                while j < n and not lines[j].strip():
                    j += 1
                if j < n and _indent(lines[j]) > base:
                    items[-1].extend([""] * (j - i))
                    tight = False
                    i = j
                    continue
                if j < n and sibling(lines[j]):
                    tight = False
                    i = j
                    continue
                break
            if _indent(line) > base:
                items[-1].append(line)
                i += 1
                continue
            if not self._starts_block(lines, i):  # lazy paragraph continuation
                items[-1].append(" " * (base + 2) + line.strip())
                i += 1
                continue
            break

        parts = []
        for item in items:
            first, rest = item[0], item[1:]
            if not rest:
                parts.append("<li>" + self.inline(first) + "</li>")
                continue
            rest_lines = textwrap.dedent("\n".join(rest)).split("\n")
            body = self._blocks([first] + rest_lines)
            if tight and body.startswith("<p>"):
                end = body.find("</p>")
                body = body[3:end] + body[end + 4 :]
            parts.append("<li>" + body + "</li>")
        tag = "ol" if ordered else "ul"
        st = f' start="{start}"' if ordered and start != 1 else ""
        out.append(f"<{tag}{st}>" + "\n".join(parts) + f"</{tag}>")
        return i


def render(text: str) -> tuple[str, list[tuple[int, str, str]]]:
    md = Markdown()
    body = md.convert(text)
    return body, md.toc
